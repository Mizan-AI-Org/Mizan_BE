"""
Manager ops digests — proactive WhatsApp briefings (Celery beat).
"""
from __future__ import annotations

import logging
import re
from datetime import time, timedelta

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

_MANAGER_ROLES = ("MANAGER", "ADMIN", "OWNER", "SUPER_ADMIN", "RESTAURANT_OWNER", "GENERAL_MANAGER")


def _phone_for(user) -> str:
    from notifications.models import NotificationPreference

    prefs = getattr(user, "notification_preferences", None)
    if prefs is None:
        try:
            prefs = NotificationPreference.objects.filter(user=user).first()
        except Exception:
            prefs = None
    raw = (getattr(prefs, "whatsapp_number", None) or getattr(user, "phone", None) or "")
    return re.sub(r"\D", "", str(raw))


def _should_send_digest(user) -> bool:
    """Managers with a phone get digests unless WhatsApp is explicitly off.

    When ``digest_enabled`` is False and ``digest_time`` is unset we still
    send (proactive MVP). Set ``whatsapp_enabled=False`` to stop. If
    ``digest_time`` is set, the sweep only delivers in that hour.
    """
    from notifications.models import NotificationPreference

    prefs = NotificationPreference.objects.filter(user=user).first()
    if prefs is not None and prefs.whatsapp_enabled is False:
        return False
    return bool(_phone_for(user))


def _compose_ops_digest(restaurant, target_date) -> str:
    from finance.models import Invoice
    from scheduling.views_agent import _compute_proactive_insights_payload
    from staff.models import StaffRequest

    insights = _compute_proactive_insights_payload(restaurant, target_date)
    lines = [f"*Miya ops digest* — {restaurant.name} ({target_date.isoformat()})"]

    for block in insights.get("insights") or []:
        summary = block.get("summary") or block.get("title")
        if summary:
            prio = block.get("priority") or "info"
            lines.append(f"• [{prio}] {summary}")

    if not insights.get("insights"):
        lines.append("• Staffing: no high-priority alerts for today.")

    open_reqs = (
        StaffRequest.objects.filter(restaurant=restaurant, status="PENDING").count()
    )
    lines.append(f"• Open staff requests: {open_reqs}")

    overdue_inv = Invoice.objects.filter(
        restaurant=restaurant,
        status=Invoice.STATUS_OPEN,
        due_date__lt=target_date,
    ).count()
    due_soon = Invoice.objects.filter(
        restaurant=restaurant,
        status=Invoice.STATUS_OPEN,
        due_date__gte=target_date,
        due_date__lte=target_date + timedelta(days=3),
    ).count()
    lines.append(f"• Invoices: {overdue_inv} overdue, {due_soon} due in 3 days")

    lines.append(
        "\nReply in chat: *what's running low?* · *today's sales* · *match invoice to PO*"
    )
    return "\n".join(lines)


@shared_task(name="scheduling.tasks_digest.manager_ops_digest_sweep")
def manager_ops_digest_sweep(period: str = "daily") -> dict:
    """
    Send WhatsApp ops digests to managers with digest_enabled + phone.

    Daily beat defaults to ~21:00. Weekly uses the same composer with a
    week-oriented intro (Sunday schedule).
    """
    from accounts.models import CustomUser, Restaurant
    from notifications.services import notification_service

    today = timezone.now().date()
    sent = 0
    skipped = 0
    failed = 0

    restaurants = list(Restaurant.objects.all()[:500])

    for restaurant in restaurants:
        managers = (
            CustomUser.objects.filter(
                restaurant_id=restaurant.id,
                is_active=True,
            )
            .filter(
                Q(role__in=_MANAGER_ROLES)
                | Q(role__icontains="MANAGER")
                | Q(role__icontains="OWNER")
                | Q(role__icontains="ADMIN")
            )
        )

        try:
            body = _compose_ops_digest(restaurant, today)
        except Exception:
            logger.exception("ops digest compose failed restaurant=%s", restaurant.id)
            failed += 1
            continue

        if period == "weekly":
            body = body.replace("*Miya ops digest*", "*Miya weekly ops digest*")

        for manager in managers:
            if not _should_send_digest(manager):
                skipped += 1
                continue
            phone = _phone_for(manager)

            # Optional: respect digest_time hour when set
            from notifications.models import NotificationPreference

            prefs = NotificationPreference.objects.filter(user=manager).first()
            if prefs and prefs.digest_time:
                now_t = timezone.localtime().time()
                dt = prefs.digest_time
                if isinstance(dt, time) and now_t.hour != dt.hour:
                    skipped += 1
                    continue

            try:
                result = notification_service.send_whatsapp_text(phone, body)
                ok = result[0] if isinstance(result, tuple) else bool(result)
                if ok:
                    sent += 1
                else:
                    failed += 1
            except Exception:
                logger.exception("ops digest send failed user=%s", manager.pk)
                failed += 1

    summary = {"period": period, "sent": sent, "skipped": skipped, "failed": failed}
    if sent or failed:
        logger.info("manager_ops_digest_sweep: %s", summary)
    return summary


@shared_task(name="scheduling.tasks_digest.manager_ops_digest_weekly")
def manager_ops_digest_weekly() -> dict:
    return manager_ops_digest_sweep(period="weekly")
