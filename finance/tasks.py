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


@shared_task(name="finance.tasks.payment_approval_stuck_sweep")
def payment_approval_stuck_sweep() -> dict:
    """
    Nudge approvers when a PayGuard rung is stuck (Miya WhatsApp reminders).

    Example: "Hi Hamza, Driss is waiting for the approval to pay an invoice of 150,000 MAD…"
    """
    from datetime import timedelta

    from finance.models import InvoicePaymentApproval
    from finance.payment_approval import get_policy, notify_current_step

    now = timezone.now()
    summary = {"checked": 0, "reminded": 0}

    qs = InvoicePaymentApproval.objects.filter(
        status=InvoicePaymentApproval.STATUS_PENDING
    ).select_related("invoice", "restaurant", "requested_by")

    for approval in qs.iterator(chunk_size=50):
        summary["checked"] += 1
        policy = get_policy(approval.restaurant)
        if not policy.get("enabled"):
            continue
        stuck_hours = int(policy.get("stuck_hours") or 4)
        max_reminders = int(policy.get("max_reminders") or 3)
        if (approval.reminder_count or 0) >= max_reminders:
            continue
        # First nudge after stuck_hours from start (or last reminder)
        anchor = approval.last_reminded_at or approval.started_at
        if not anchor:
            continue
        if now - anchor < timedelta(hours=stuck_hours):
            continue
        try:
            n = notify_current_step(approval, is_reminder=True)
            if n:
                summary["reminded"] += 1
        except Exception:
            logger.exception("PayGuard stuck sweep failed approval=%s", approval.pk)

    if summary["reminded"]:
        logger.info("payment_approval_stuck_sweep: %s", summary)
    return summary
