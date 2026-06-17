"""Scheduled payroll & compliance jobs."""
from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(name="payroll.tasks.compliance_reminder_sweep")
def compliance_reminder_sweep() -> dict:
    """Notify managers about upcoming CNSS / tax / payroll-close deadlines."""
    from accounts.models import CustomUser, Restaurant
    from notifications.models import Notification
    from notifications.services import notification_service
    from payroll.models import ComplianceReminder

    today = timezone.now().date()
    summary = {"notified": 0, "checked": 0}

    for restaurant in Restaurant.objects.filter(is_active=True).iterator(chunk_size=50):
        qs = ComplianceReminder.objects.filter(
            restaurant=restaurant,
            status=ComplianceReminder.STATUS_UPCOMING,
            due_date__lte=today + timedelta(days=14),
        )
        for reminder in qs.iterator():
            summary["checked"] += 1
            if not reminder.is_due_soon:
                continue
            managers = CustomUser.objects.filter(
                restaurant=restaurant,
                role__in=["MANAGER", "ADMIN", "OWNER", "SUPER_ADMIN"],
                is_active=True,
            )
            body = f"📅 {reminder.title} — due {reminder.due_date.isoformat()}. {reminder.description[:200]}"
            for manager in managers:
                try:
                    notif = Notification.objects.create(
                        recipient=manager,
                        title="Compliance reminder",
                        message=body.replace("📅 ", ""),
                        notification_type="COMPLIANCE_REMINDER",
                        data={"compliance_id": str(reminder.id), "due_date": reminder.due_date.isoformat()},
                    )
                    notification_service.send_custom_notification(
                        recipient=manager,
                        notification=notif,
                        message=notif.message,
                        notification_type="COMPLIANCE_REMINDER",
                        title=notif.title,
                        channels=["app", "push"],
                    )
                    phone = getattr(manager, "phone", "") or ""
                    if phone.strip():
                        notification_service.send_whatsapp_text(phone, body)
                except Exception:
                    logger.exception("Compliance notify failed for manager %s", manager.pk)
            reminder.status = ComplianceReminder.STATUS_NOTIFIED
            reminder.last_notified_at = timezone.now()
            reminder.save(update_fields=["status", "last_notified_at", "updated_at"])
            summary["notified"] += 1

    if summary["notified"]:
        logger.info("compliance_reminder_sweep: %s", summary)
    return summary
