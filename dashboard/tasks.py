"""
Auto follow-up sweep for dashboard tasks.

When a manager assigns a task via Miya, the assignee receives a WhatsApp
notification. If the task stays PENDING (no acknowledgement or status
change), Miya automatically follows up on behalf of the manager.

Key constraint: **Meta's 24-hour messaging window.** WhatsApp only allows
free-form text messages within 24 hours of the *user's last inbound
message*. After that, only pre-approved template messages can be sent.
Since we can't guarantee the staff sent a message recently, all follow-ups
must land within 24 hours of the original notification (which *was* inside
a valid conversation window).

Follow-up schedule (relative to `whatsapp_notified_at`):
  - 1st follow-up: ~4 hours after notification
  - 2nd follow-up: ~12 hours after notification (still well inside 24h)
  - No follow-ups after 20 hours to leave a safety margin before the
    24-hour window closes.

The sweep runs every 15 minutes via Celery Beat. Each task gets at most
`follow_up_max` nudges (default 2). Tasks that are completed, cancelled,
or in-progress are skipped. URGENT tasks get their first follow-up sooner
(~2 hours).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

FOLLOW_UP_SCHEDULE_HOURS = {
    0: {
        'URGENT': 2,
        'HIGH': 3,
        'MEDIUM': 4,
        'LOW': 6,
    },
    1: {
        'URGENT': 8,
        'HIGH': 10,
        'MEDIUM': 12,
        'LOW': 14,
    },
}

MAX_WINDOW_HOURS = 20


def _build_follow_up_message(task, follow_up_number: int) -> str:
    """Build a contextual, friendly follow-up message.

    Kept short and professional — these land on the staff member's WhatsApp
    on behalf of the manager. Written in a neutral tone that works in any
    language (Miya will translate when appropriate via the agent layer, but
    these direct Celery messages go out in English as a fallback).
    """
    title = task.title or 'your assigned task'

    if follow_up_number == 1:
        due = ''
        if task.due_date:
            due = f' (due {task.due_date.strftime("%b %d")})'
        return (
            f"Hi! Just checking in on: *{title}*{due}\n\n"
            f"Could you update the status or let me know how it's going? "
            f"Reply here or mark it in-progress from your dashboard."
        )

    return (
        f"Friendly reminder about: *{title}*\n\n"
        f"Your manager is waiting for an update. "
        f"Please reply with your progress or mark the task done when complete."
    )


@shared_task(name='dashboard.tasks.task_follow_up_sweep')
def task_follow_up_sweep() -> dict:
    """Sweep pending dashboard tasks and send WhatsApp follow-ups.

    Runs every 15 min via Celery Beat. Idempotent — each task gets at most
    N follow-ups, tracked by `follow_up_count`.
    """
    from dashboard.models import Task
    from notifications.services import NotificationService

    now = timezone.now()
    ns = NotificationService()
    summary = {'checked': 0, 'followed_up': 0, 'skipped_no_phone': 0, 'errors': 0}

    candidates = Task.objects.filter(
        follow_up_enabled=True,
        status='PENDING',
        whatsapp_notified_at__isnull=False,
        assigned_to__isnull=False,
    ).filter(
        follow_up_count__lt=models_f('follow_up_max'),
    ).select_related('assigned_to', 'restaurant')

    for task in candidates.iterator(chunk_size=100):
        summary['checked'] += 1

        notified_at = task.whatsapp_notified_at
        hours_since = (now - notified_at).total_seconds() / 3600

        if hours_since >= MAX_WINDOW_HOURS:
            continue

        current_count = task.follow_up_count
        if current_count >= task.follow_up_max:
            continue

        schedule = FOLLOW_UP_SCHEDULE_HOURS.get(current_count, {})
        trigger_hours = schedule.get(task.priority or 'MEDIUM', 6)

        if hours_since < trigger_hours:
            continue

        if task.last_follow_up_at:
            hours_since_last = (now - task.last_follow_up_at).total_seconds() / 3600
            if hours_since_last < 2:
                continue

        assignee = task.assigned_to
        phone = getattr(assignee, 'phone', None) or ''
        phone = ''.join(c for c in phone if c.isdigit() or c == '+')
        if not phone:
            summary['skipped_no_phone'] += 1
            continue

        message = _build_follow_up_message(task, current_count + 1)

        try:
            ok, _ = ns.send_whatsapp_text(phone, message)
            if ok:
                task.follow_up_count = current_count + 1
                task.last_follow_up_at = now
                task.save(update_fields=['follow_up_count', 'last_follow_up_at', 'updated_at'])
                summary['followed_up'] += 1
                logger.info(
                    'Task follow-up #%d sent for task %s to %s',
                    task.follow_up_count, task.pk, phone,
                )
            else:
                summary['errors'] += 1
                logger.warning('Follow-up send failed for task %s', task.pk)
        except Exception:
            summary['errors'] += 1
            logger.exception('Follow-up error for task %s', task.pk)

    if summary['followed_up']:
        logger.info('task_follow_up_sweep: %s', summary)
    return summary


def models_f(field_name: str):
    """Wrap Django F() to avoid import at module level."""
    from django.db.models import F
    return F(field_name)
