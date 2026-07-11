"""
Shared WhatsApp follow-up + manager escalation helpers for staff requests
and dashboard tasks.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.utils import timezone

logger = logging.getLogger(__name__)

# Hours after initial WhatsApp notify before each follow-up nudge.
FOLLOW_UP_SCHEDULE_HOURS: dict[int, dict[str, int]] = {
    0: {"URGENT": 2, "HIGH": 3, "MEDIUM": 4, "LOW": 6},
    1: {"URGENT": 8, "HIGH": 10, "MEDIUM": 12, "LOW": 14},
}

MAX_WINDOW_HOURS = 20
MIN_HOURS_BETWEEN_FOLLOW_UPS = 2


def normalize_phone(raw: str | None) -> str:
    if not raw:
        return ""
    return "".join(c for c in str(raw) if c.isdigit() or c == "+")


def build_staff_request_follow_up_message(req, follow_up_number: int) -> str:
    subject = (req.subject or "your assigned request").strip()
    if follow_up_number == 1:
        return (
            f"Hi! Just checking in on: *{subject}* "
            f"({req.category.lower() if req.category else 'request'})\n\n"
            f"Could you update the status or let me know how it's going? "
            f"Reply here or open the inbox in Mizan."
        )
    return (
        f"Friendly reminder about: *{subject}*\n\n"
        f"Your manager is waiting for an update on this {req.priority.lower()} request. "
        f"Please reply with your progress or mark it handled in Mizan."
    )


def build_task_follow_up_message(task, follow_up_number: int) -> str:
    title = task.title or "your assigned task"
    if follow_up_number == 1:
        due = ""
        if task.due_date:
            due = f" (due {task.due_date.strftime('%b %d')})"
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


def should_send_follow_up(
    *,
    notified_at,
    priority: str,
    follow_up_count: int,
    follow_up_max: int,
    last_follow_up_at,
    now=None,
    follow_up_first_hours: int | None = None,
) -> bool:
    now = now or timezone.now()
    if not notified_at or follow_up_count >= follow_up_max:
        return False
    hours_since = (now - notified_at).total_seconds() / 3600
    if hours_since >= MAX_WINDOW_HOURS:
        return False
    if follow_up_count == 0 and follow_up_first_hours is not None:
        try:
            trigger_hours = max(1, min(20, int(follow_up_first_hours)))
        except (TypeError, ValueError):
            trigger_hours = FOLLOW_UP_SCHEDULE_HOURS.get(0, {}).get(
                (priority or "MEDIUM").upper(), 6
            )
    else:
        schedule = FOLLOW_UP_SCHEDULE_HOURS.get(follow_up_count, {})
        trigger_hours = schedule.get((priority or "MEDIUM").upper(), 6)
    if hours_since < trigger_hours:
        return False
    if last_follow_up_at:
        hours_since_last = (now - last_follow_up_at).total_seconds() / 3600
        if hours_since_last < MIN_HOURS_BETWEEN_FOLLOW_UPS:
            return False
    return True


def escalate_staff_request_to_managers(req, *, reason: str) -> dict[str, Any]:
    """Notify managers in-app + WhatsApp when assignee follow-ups are exhausted."""
    from accounts.models import CustomUser
    from notifications.models import Notification
    from notifications.services import notification_service

    if getattr(req, "escalated_at", None):
        return {"escalated": False, "reason": "already_escalated"}

    subject = req.subject or "Staff request"
    assignee_name = ""
    if req.assignee:
        assignee_name = req.assignee.get_full_name() or req.assignee.email or "Assignee"
    body = (
        f"⚠️ Escalation: *{subject}* ({req.priority}) still pending after follow-ups. "
        f"{reason}"
        + (f" Assigned to {assignee_name}." if assignee_name else "")
    )
    wa_sent = 0
    app_sent = 0
    managers = CustomUser.objects.filter(
        restaurant=req.restaurant,
        role__in=["MANAGER", "ADMIN", "SUPER_ADMIN", "OWNER"],
        is_active=True,
    )
    for manager in managers:
        try:
            notif = Notification.objects.create(
                recipient=manager,
                title="Urgent follow-up needed",
                message=body.replace("*", ""),
                notification_type="STAFF_REQUEST_ESCALATION",
                priority="URGENT" if req.priority == "URGENT" else req.priority,
                data={
                    "staff_request_id": str(req.id),
                    "route": f"/dashboard/staff-requests/{req.id}",
                    "status": req.status,
                    "category": req.category,
                },
            )
            notification_service.send_custom_notification(
                recipient=manager,
                notification=notif,
                message=notif.message,
                notification_type="STAFF_REQUEST_ESCALATION",
                title=notif.title,
                channels=["app", "push"],
            )
            app_sent += 1
        except Exception:
            logger.exception("Escalation in-app notify failed for manager %s", manager.pk)

        phone = normalize_phone(getattr(manager, "phone", None))
        if phone:
            try:
                ok, _ = notification_service.send_whatsapp_text(phone, body)
                if ok:
                    wa_sent += 1
            except Exception:
                logger.exception("Escalation WhatsApp failed for manager %s", manager.pk)

    req.escalated_at = timezone.now()
    req.save(update_fields=["escalated_at", "updated_at"])
    return {"escalated": True, "managers_app": app_sent, "managers_whatsapp": wa_sent}


def escalate_task_to_managers(task, *, reason: str) -> dict[str, Any]:
    from accounts.models import CustomUser
    from notifications.models import Notification
    from notifications.services import notification_service

    if getattr(task, "escalated_at", None):
        return {"escalated": False, "reason": "already_escalated"}

    assignee_name = ""
    if task.assigned_to:
        assignee_name = (
            f"{(task.assigned_to.first_name or '').strip()} {(task.assigned_to.last_name or '').strip()}".strip()
            or task.assigned_to.email
            or "Assignee"
        )
    body = (
        f"⚠️ Escalation: task *{task.title}* ({task.priority}) still pending after follow-ups. "
        f"{reason}"
        + (f" Assigned to {assignee_name}." if assignee_name else "")
    )
    wa_sent = 0
    app_sent = 0
    managers = CustomUser.objects.filter(
        restaurant=task.restaurant,
        role__in=["MANAGER", "ADMIN", "SUPER_ADMIN", "OWNER"],
        is_active=True,
    )
    for manager in managers:
        try:
            notif = Notification.objects.create(
                recipient=manager,
                title="Task follow-up exhausted",
                message=body.replace("*", ""),
                notification_type="TASK_ESCALATION",
                priority="URGENT" if task.priority == "URGENT" else task.priority,
                data={"task_id": str(task.id), "route": "/dashboard"},
            )
            notification_service.send_custom_notification(
                recipient=manager,
                notification=notif,
                message=notif.message,
                notification_type="TASK_ESCALATION",
                title=notif.title,
                channels=["app", "push"],
            )
            app_sent += 1
        except Exception:
            logger.exception("Task escalation in-app failed for manager %s", manager.pk)

        phone = normalize_phone(getattr(manager, "phone", None))
        if phone:
            try:
                ok, _ = notification_service.send_whatsapp_text(phone, body)
                if ok:
                    wa_sent += 1
            except Exception:
                logger.exception("Task escalation WhatsApp failed for manager %s", manager.pk)

    task.escalated_at = timezone.now()
    task.save(update_fields=["escalated_at", "updated_at"])
    return {"escalated": True, "managers_app": app_sent, "managers_whatsapp": wa_sent}
