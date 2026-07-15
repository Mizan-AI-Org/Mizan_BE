"""Scheduled payroll & compliance jobs."""
from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

# Re-notify at most this often while a document stays in the reminder window
_DOC_NOTIFY_COOLDOWN = timedelta(days=3)


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


@shared_task(name="payroll.tasks.compliance_document_expiry_sweep")
def compliance_document_expiry_sweep() -> dict:
    """
    Remind owners/managers about restaurant documents nearing (or past) expiry.
    Covers insurance, hygiene certificates, fire extinguishers, business registration, etc.
    """
    from accounts.models import CustomUser, Restaurant
    from notifications.models import Notification, NotificationPreference
    from notifications.services import notification_service
    from payroll.models import ComplianceDocument
    from payroll.services.compliance_documents import days_until, document_urgency

    today = timezone.now().date()
    now = timezone.now()
    summary = {"notified_docs": 0, "checked": 0, "managers_pinged": 0}

    for restaurant in Restaurant.objects.filter(is_active=True).iterator(chunk_size=50):
        qs = ComplianceDocument.objects.filter(
            restaurant=restaurant,
            status__in=[ComplianceDocument.STATUS_ACTIVE, ComplianceDocument.STATUS_EXPIRED],
        ).exclude(expires_at__isnull=True)

        for doc in qs.iterator():
            summary["checked"] += 1
            if not doc.is_in_reminder_window:
                continue
            if doc.last_notified_at and (now - doc.last_notified_at) < _DOC_NOTIFY_COOLDOWN:
                continue

            from core.i18n import get_effective_language, tr

            dleft = days_until(doc.expires_at, today)
            urgency = document_urgency(doc.expires_at, today)
            if dleft is not None and dleft < 0:
                doc.status = ComplianceDocument.STATUS_EXPIRED

            managers = CustomUser.objects.filter(
                restaurant=restaurant,
                role__in=["MANAGER", "ADMIN", "OWNER", "SUPER_ADMIN"],
                is_active=True,
            )
            pinged = 0
            for manager in managers:
                try:
                    prefs = NotificationPreference.objects.filter(user=manager).first()
                    if prefs and not getattr(prefs, "compliance_notifications", True):
                        continue
                    lang = get_effective_language(user=manager, restaurant=restaurant)
                    date_s = doc.expires_at.isoformat() if doc.expires_at else ""
                    if dleft is not None and dleft < 0:
                        when = tr(
                            "compliance.expiry.when_ago", lang, n=-dleft, date=date_s
                        )
                    elif dleft == 0:
                        when = tr("compliance.expiry.when_today", lang, date=date_s)
                    else:
                        when = tr(
                            "compliance.expiry.when_in", lang, n=dleft or 0, date=date_s
                        )
                    when_plain = when.replace("*", "")
                    body = tr(
                        "compliance.expiry.body",
                        lang,
                        title=doc.title,
                        when=when,
                        doc_type=doc.get_document_type_display(),
                    )
                    title = tr("compliance.expiry.title", lang)
                    app_msg = tr(
                        "compliance.expiry.app",
                        lang,
                        title=doc.title,
                        when_plain=when_plain,
                    )
                    notif = Notification.objects.create(
                        recipient=manager,
                        title=title,
                        message=app_msg,
                        notification_type="COMPLIANCE_REMINDER",
                        data={
                            "compliance_document_id": str(doc.id),
                            "expires_at": doc.expires_at.isoformat() if doc.expires_at else None,
                            "urgency": urgency,
                        },
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
                    wa_ok = True
                    if prefs and getattr(prefs, "whatsapp_enabled", True) is False:
                        wa_ok = False
                    if wa_ok and phone.strip():
                        notification_service.send_whatsapp_text(phone, body)
                    pinged += 1
                    summary["managers_pinged"] += 1
                except Exception:
                    logger.exception(
                        "compliance_document notify failed restaurant=%s manager=%s",
                        restaurant.pk,
                        manager.pk,
                    )

            if pinged:
                doc.last_notified_at = now
                doc.save(update_fields=["status", "last_notified_at", "updated_at"])
                summary["notified_docs"] += 1

    if summary["notified_docs"]:
        logger.info("compliance_document_expiry_sweep: %s", summary)
    return summary
