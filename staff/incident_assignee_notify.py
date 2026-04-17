"""
WhatsApp notification to the assignee when a safety incident is assigned to them.
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:
    from staff.models_task import SafetyConcernReport

logger = logging.getLogger(__name__)


def _build_assignee_message(ticket: "SafetyConcernReport") -> str:
    restaurant_name = (
        ticket.restaurant.name if getattr(ticket, "restaurant", None) else "Restaurant"
    )
    itype = (ticket.incident_type or "General").strip()
    sev = (ticket.severity or "MEDIUM").strip()
    title = (ticket.title or "Incident").strip()
    desc = (ticket.description or "").strip()
    if len(desc) > 600:
        desc = desc[:597] + "…"

    reporter_bits = []
    if ticket.reporter_id and getattr(ticket, "reporter", None):
        r = ticket.reporter
        name = (f"{r.first_name or ''} {r.last_name or ''}").strip() or (r.email or "")
        if name:
            reporter_bits.append(f"*Reported by:* {name}")

    front = getattr(settings, "FRONTEND_URL", "") or ""
    dash = f"{front.rstrip('/')}/dashboard/safety" if front else ""

    lines = [
        "🔔 *Miya — new incident assigned to you*",
        "",
        f"*Restaurant:* {restaurant_name}",
        f"*Category:* {itype}",
        f"*Severity:* {sev}",
        f"*Title:* {title}",
    ]
    if reporter_bits:
        lines.extend(["", *reporter_bits])
    lines.extend(["", "*Description:*", desc or "—", ""])
    lines.append(f"*Ticket:* `{str(ticket.id)[:8]}…`")
    if dash:
        lines.extend(["", f"Open: {dash}"])
    return "\n".join(lines)


def notify_assignee_whatsapp_for_incident(ticket: "SafetyConcernReport") -> None:
    """
    Send WhatsApp to ticket.assigned_to. No-op if no phone, WhatsApp not configured, or assignee is reporter.
    """
    assignee = getattr(ticket, "assigned_to", None)
    if not assignee:
        return
    if ticket.reporter_id and assignee.id == ticket.reporter_id:
        return

    phone = getattr(assignee, "phone", None) or ""
    if not str(phone).strip():
        logger.info(
            "incident_assignee_notify: no phone for assignee %s, skip WhatsApp",
            assignee.id,
        )
        return

    try:
        from notifications.services import notification_service, normalize_whatsapp_phone

        digits, phone_err = normalize_whatsapp_phone(phone)
        if phone_err:
            logger.warning(
                "incident_assignee_notify: bad phone for assignee %s: %s",
                assignee.id,
                phone_err,
            )
            return

        token = getattr(settings, "WHATSAPP_ACCESS_TOKEN", None)
        phone_id = getattr(settings, "WHATSAPP_PHONE_NUMBER_ID", None)
        if not token or not phone_id:
            logger.info("incident_assignee_notify: WhatsApp not configured, skip")
            return

        body = _build_assignee_message(ticket)
        ok, meta = notification_service.send_whatsapp_text(digits, body)
        if not ok:
            logger.warning(
                "incident_assignee_notify: WhatsApp send failed for %s: %s",
                assignee.id,
                meta,
            )
    except Exception:
        logger.exception("incident_assignee_notify: failed for ticket %s", ticket.pk)


def schedule_notify_assignee_whatsapp_for_incident(ticket_pk) -> None:
    """Reload ticket in a daemon thread so HTTP handlers are not blocked by Meta API."""

    def _run():
        try:
            from staff.models_task import SafetyConcernReport

            ticket = SafetyConcernReport.objects.select_related(
                "assigned_to", "reporter", "restaurant"
            ).get(pk=ticket_pk)
            notify_assignee_whatsapp_for_incident(ticket)
        except Exception:
            logger.exception(
                "schedule_notify_assignee_whatsapp: failed for ticket %s", ticket_pk
            )

    threading.Thread(target=_run, daemon=True).start()
