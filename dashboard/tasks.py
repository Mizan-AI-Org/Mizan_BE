"""
Auto follow-up sweep for dashboard tasks.

When a manager assigns a task via Miya, the assignee receives a WhatsApp
notification. If the task stays PENDING, Miya automatically follows up on
behalf of the manager and escalates to managers when follow-ups are exhausted.
"""

from __future__ import annotations

import logging

from datetime import datetime, timedelta

from celery import shared_task
from django.db.models import F
from django.utils import timezone

from staff.follow_up_helpers import (
    build_task_follow_up_message,
    escalate_task_to_managers,
    normalize_phone,
    should_send_follow_up,
)

logger = logging.getLogger(__name__)


@shared_task(name='dashboard.tasks.task_follow_up_sweep')
def task_follow_up_sweep() -> dict:
    """Sweep pending dashboard tasks — WhatsApp follow-ups + manager escalation."""
    from dashboard.models import Task
    from notifications.services import NotificationService

    now = timezone.now()
    ns = NotificationService()
    summary = {
        'checked': 0,
        'followed_up': 0,
        'escalated': 0,
        'skipped_no_phone': 0,
        'errors': 0,
    }

    candidates = (
        Task.objects.filter(
            follow_up_enabled=True,
            status='PENDING',
            whatsapp_notified_at__isnull=False,
            assigned_to__isnull=False,
            escalated_at__isnull=True,
        )
        .filter(follow_up_count__lt=F('follow_up_max'))
        .select_related('assigned_to', 'restaurant')
    )

    for task in candidates.iterator(chunk_size=100):
        summary['checked'] += 1
        assignee = task.assigned_to
        phone = normalize_phone(getattr(assignee, 'phone', None))
        if not phone:
            summary['skipped_no_phone'] += 1
            continue

        if should_send_follow_up(
            notified_at=task.whatsapp_notified_at,
            priority=task.priority or 'MEDIUM',
            follow_up_count=task.follow_up_count,
            follow_up_max=task.follow_up_max,
            last_follow_up_at=task.last_follow_up_at,
            now=now,
            follow_up_first_hours=getattr(task, "follow_up_first_hours", None),
        ):
            message = build_task_follow_up_message(task, task.follow_up_count + 1)
            try:
                ok, _ = ns.send_whatsapp_text(phone, message)
                if ok:
                    task.follow_up_count += 1
                    task.last_follow_up_at = now
                    task.save(update_fields=['follow_up_count', 'last_follow_up_at', 'updated_at'])
                    summary['followed_up'] += 1
                    logger.info(
                        'Task follow-up #%s sent for task %s',
                        task.follow_up_count,
                        task.pk,
                    )
                else:
                    summary['errors'] += 1
            except Exception:
                summary['errors'] += 1
                logger.exception('Follow-up error for task %s', task.pk)
            continue

        # Escalate when max follow-ups sent and still pending inside window
        if (
            task.follow_up_count >= task.follow_up_max
            and task.whatsapp_notified_at
            and (now - task.whatsapp_notified_at).total_seconds() / 3600 < 24
        ):
            try:
                result = escalate_task_to_managers(
                    task,
                    reason="Automatic follow-ups to the assignee did not resolve this.",
                )
                if result.get('escalated'):
                    summary['escalated'] += 1
            except Exception:
                summary['errors'] += 1
                logger.exception('Task escalation failed for %s', task.pk)

    if summary['followed_up'] or summary['escalated']:
        logger.info('task_follow_up_sweep: %s', summary)
    return summary


@shared_task(name='dashboard.tasks.snapshot_staff_daily_progress')
def snapshot_staff_daily_progress_task() -> dict:
    """
    Archive yesterday's per-staff task progress for every restaurant.
    Runs shortly after midnight so the live widget can reset to today only.
    """
    from accounts.models import Restaurant
    from dashboard.services.staff_daily_progress import (
        close_stale_shift_checklists,
        snapshot_staff_daily_progress,
    )

    report_date = timezone.localdate() - timedelta(days=1)
    summary = {"date": str(report_date), "restaurants": 0, "staff_rows": 0, "errors": 0, "stale_checklists_closed": 0}

    for restaurant in Restaurant.objects.all().iterator(chunk_size=50):
        try:
            summary["stale_checklists_closed"] += close_stale_shift_checklists(restaurant=restaurant)
            count = snapshot_staff_daily_progress(restaurant, report_date)
            summary["restaurants"] += 1
            summary["staff_rows"] += count
        except Exception:
            summary["errors"] += 1
            logger.exception(
                "staff daily progress snapshot failed restaurant=%s date=%s",
                restaurant.id,
                report_date,
            )

    if summary["staff_rows"] or summary["errors"]:
        logger.info("snapshot_staff_daily_progress: %s", summary)
    return summary


# Critical Ops Live items older than this (hours) get a manager nudge.
_OPS_LIVE_STALE_HOURS = {
    "URGENT": 2,
    "HIGH": 4,
    "CRITICAL": 2,
}
_OPS_LIVE_STALE_DEFAULT_HOURS = 6
_OPS_LIVE_STALE_DEDUPE_HOURS = 6


@shared_task(name="dashboard.tasks.ops_live_stale_sweep")
def ops_live_stale_sweep() -> dict:
    """Nudge managers when critical Operations Live items sit unresolved."""
    from accounts.models import Restaurant
    from dashboard.api.operations_live import (
        build_operations_live_payload,
        notify_managers_urgent,
    )
    from django.core.cache import cache

    now = timezone.now()
    summary = {
        "restaurants": 0,
        "critical_seen": 0,
        "nudged": 0,
        "skipped_recent": 0,
        "errors": 0,
    }

    for restaurant in Restaurant.objects.all().iterator(chunk_size=40):
        summary["restaurants"] += 1
        try:
            feed = build_operations_live_payload(
                restaurant, limit=30, urgent_only=True
            )
            rows = list(feed.get("pending") or []) + list(feed.get("in_progress") or [])
            for row in rows:
                summary["critical_seen"] += 1
                priority = str(row.get("priority") or "MEDIUM").upper()
                hours = _OPS_LIVE_STALE_HOURS.get(priority, _OPS_LIVE_STALE_DEFAULT_HOURS)
                created_raw = row.get("created_at") or ""
                try:
                    created = datetime.fromisoformat(
                        str(created_raw).replace("Z", "+00:00")
                    )
                    if timezone.is_naive(created):
                        created = timezone.make_aware(
                            created, timezone.get_current_timezone()
                        )
                except Exception:
                    continue
                age_h = (now - created).total_seconds() / 3600.0
                if age_h < hours:
                    continue

                task_id = str(row.get("id") or "")
                dedupe_key = f"ops_live_stale:{restaurant.id}:{task_id}"
                if cache.get(dedupe_key):
                    summary["skipped_recent"] += 1
                    continue

                title = (row.get("title") or row.get("operation") or "Critical item")[:120]
                age_label = row.get("age_label") or f"{int(age_h)}h"
                message = (
                    f"⏰ Still open on Operations Live ({age_label}): {title}. "
                    "Please update status or reassign."
                )
                notify_managers_urgent(
                    restaurant,
                    message=message,
                    task_id=task_id or None,
                    channels=["app", "whatsapp"],
                )
                cache.set(
                    dedupe_key,
                    "1",
                    int(_OPS_LIVE_STALE_DEDUPE_HOURS * 3600),
                )
                summary["nudged"] += 1
        except Exception:
            summary["errors"] += 1
            logger.exception("ops_live_stale_sweep failed restaurant=%s", restaurant.id)

    if summary["nudged"] or summary["errors"]:
        logger.info("ops_live_stale_sweep: %s", summary)
    return summary
