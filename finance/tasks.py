"""Finance scheduled jobs — overdue invoice reminders."""
from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(name="finance.tasks.invoice_overdue_reminder_sweep")
def invoice_overdue_reminder_sweep() -> dict:
    """Notify managers about OPEN invoices due within 3 days or already overdue."""
    from accounts.models import CustomUser
    from finance.models import Invoice
    from notifications.models import Notification
    from notifications.services import notification_service

    today = timezone.now().date()
    horizon = today + timedelta(days=3)
    summary = {"notified": 0, "checked": 0}

    invoices = Invoice.objects.filter(
        status=Invoice.STATUS_OPEN,
        due_date__lte=horizon,
    ).select_related("restaurant")

    for inv in invoices.iterator(chunk_size=100):
        summary["checked"] += 1
        already = Notification.objects.filter(
            notification_type="INVOICE_REMINDER",
            data__invoice_id=str(inv.id),
            created_at__date=today,
        ).exists()
        if already:
            continue

        managers = CustomUser.objects.filter(
            restaurant_id=inv.restaurant_id,
            role__in=["MANAGER", "ADMIN", "OWNER"],
            is_active=True,
        )
        overdue = inv.due_date < today
        label = "OVERDUE" if overdue else f"due {inv.due_date.isoformat()}"
        body = (
            f"💰 Invoice reminder: {inv.vendor_name}"
            + (f" #{inv.invoice_number}" if inv.invoice_number else "")
            + f" — {inv.amount} {inv.currency} ({label})."
        )
        for manager in managers:
            try:
                Notification.objects.create(
                    recipient=manager,
                    title="Invoice payment reminder",
                    message=body.replace("💰 ", ""),
                    notification_type="INVOICE_REMINDER",
                    data={"invoice_id": str(inv.id)},
                )
                notification_service.send_custom_notification(
                    recipient=manager,
                    message=body.replace("💰 ", ""),
                    notification_type="INVOICE_REMINDER",
                    title="Invoice payment reminder",
                    channels=["app", "push"],
                )
                if manager.phone:
                    notification_service.send_whatsapp_text(manager.phone, body)
            except Exception:
                logger.exception("Invoice reminder failed for manager %s", manager.pk)

        summary["notified"] += 1

    if summary["notified"]:
        logger.info("invoice_overdue_reminder_sweep: %s", summary)
    return summary
