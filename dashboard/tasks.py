"""
Auto follow-up sweep for dashboard tasks.

When a manager assigns a task via Miya, the assignee receives a WhatsApp
notification. If the task stays PENDING, Miya automatically follows up on
behalf of the manager and escalates to managers when follow-ups are exhausted.
"""

from __future__ import annotations

import logging

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
