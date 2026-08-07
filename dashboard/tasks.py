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


_MANAGER_BRIEFING_ROLES = (
    "MANAGER",
    "ADMIN",
    "OWNER",
    "SUPER_ADMIN",
    "RESTAURANT_OWNER",
    "GENERAL_MANAGER",
)


def _manager_briefing_phone(user) -> str:
    from notifications.models import NotificationPreference

    prefs = getattr(user, "notification_preferences", None)
    if prefs is None:
        try:
            prefs = NotificationPreference.objects.filter(user=user).first()
        except Exception:
            prefs = None
    raw = (getattr(prefs, "whatsapp_number", None) or getattr(user, "phone", None) or "")
    return normalize_phone(str(raw))


def _manager_wants_ops_briefing(user) -> bool:
    from notifications.models import NotificationPreference

    prefs = NotificationPreference.objects.filter(user=user).first()
    if prefs is not None and prefs.whatsapp_enabled is False:
        return False
    return bool(_manager_briefing_phone(user))


@shared_task(name="dashboard.tasks.operations_live_manager_briefing_sweep")
def operations_live_manager_briefing_sweep(period: str = "morning") -> dict:
    """
    Proactive Operations Live briefings for managers on WhatsApp.

    - morning (07:00): new demands + in progress, critical first
    - evening (21:00): same snapshot + items completed today
    """
    from accounts.models import CustomUser, Restaurant
    from django.core.cache import cache
    from django.db.models import Q
    from notifications.services import notification_service

    from dashboard.api.operations_live import (
        build_operations_live_payload,
        format_operations_live_briefing,
    )

    period = (period or "morning").strip().lower()
    if period not in {"morning", "evening"}:
        period = "morning"

    today = timezone.localdate().isoformat()
    summary = {
        "period": period,
        "sent": 0,
        "skipped": 0,
        "failed": 0,
        "restaurants": 0,
    }

    for restaurant in Restaurant.objects.all().iterator(chunk_size=40):
        summary["restaurants"] += 1
        try:
            payload = build_operations_live_payload(restaurant, limit=40)
            body = format_operations_live_briefing(payload, period=period)
        except Exception:
            summary["failed"] += 1
            logger.exception(
                "operations_live_manager_briefing compose failed restaurant=%s",
                restaurant.id,
            )
            continue

        managers = CustomUser.objects.filter(
            restaurant_id=restaurant.id,
            is_active=True,
        ).filter(
            Q(role__in=_MANAGER_BRIEFING_ROLES)
            | Q(role__icontains="MANAGER")
            | Q(role__icontains="OWNER")
            | Q(role__icontains="ADMIN")
        )

        for manager in managers:
            if not _manager_wants_ops_briefing(manager):
                summary["skipped"] += 1
                continue

            dedupe_key = f"ops_live_brief:{period}:{restaurant.id}:{manager.id}:{today}"
            if cache.get(dedupe_key):
                summary["skipped"] += 1
                continue

            phone = _manager_briefing_phone(manager)
            if not phone or len(phone) < 8:
                summary["skipped"] += 1
                continue

            try:
                result = notification_service.send_whatsapp_text(phone, body)
                ok = result[0] if isinstance(result, tuple) else bool(result)
                if ok:
                    cache.set(dedupe_key, "1", 86400)
                    summary["sent"] += 1
                else:
                    summary["failed"] += 1
            except Exception:
                summary["failed"] += 1
                logger.exception(
                    "operations_live_manager_briefing send failed user=%s period=%s",
                    manager.pk,
                    period,
                )

    if summary["sent"] or summary["failed"]:
        logger.info("operations_live_manager_briefing_sweep: %s", summary)
    return summary


@shared_task(name="dashboard.tasks.operations_live_morning_brief")
def operations_live_morning_brief() -> dict:
    """Phase 6 Daily Operations Intelligence (prefs + severity + dedupe)."""
    from miya.services.intelligence.proactive.tasks import daily_ops_intelligence_sweep

    return daily_ops_intelligence_sweep(period="morning")


@shared_task(name="dashboard.tasks.operations_live_evening_debrief")
def operations_live_evening_debrief() -> dict:
    return operations_live_manager_briefing_sweep(period="evening")
