"""
WhatsApp notification to the assignee when a safety incident is assigned to them.

Fans out to additional category owners as "informed" when multiple owners
are configured via ``resolve_all_assignees_for_incident_type``.
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:
    from staff.models_task import SafetyConcernReport

logger = logging.getLogger(__name__)


def _build_assignee_message(ticket: "SafetyConcernReport", *, informed: bool = False) -> str:
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
    dash = f"{front.rstrip('/')}/dashboard/analytics?tab=incidents" if front else ""

    primary = getattr(ticket, "assigned_to", None)
    primary_name = ""
    if primary:
        primary_name = (
            f"{primary.first_name or ''} {primary.last_name or ''}".strip()
            or (primary.email or "")
        )

    if informed:
        headline = "ℹ️ *Miya — incident FYI (category owner)*"
        assign_line = f"*Assigned to:* {primary_name or 'a teammate'}"
    else:
        headline = "🔔 *Miya — new incident assigned to you*"
        assign_line = ""

    lines = [
        headline,
        "",
        f"*Restaurant:* {restaurant_name}",
        f"*Category:* {itype}",
        f"*Severity:* {sev}",
        f"*Title:* {title}",
    ]
    if assign_line:
        lines.append(assign_line)
    if reporter_bits:
        lines.extend(["", *reporter_bits])
    lines.extend(["", "*Description:*", desc or "—", ""])
    lines.append(f"*Ticket:* `{str(ticket.id)[:8]}…`")
    if dash:
        lines.extend(["", f"Open: {dash}"])
    return "\n".join(lines)


def _send_whatsapp(user, body: str) -> bool:
    phone = getattr(user, "phone", None) or ""
    if not str(phone).strip():
        logger.info(
            "incident_assignee_notify: no phone for user %s, skip WhatsApp",
            getattr(user, "id", None),
        )
        return False

    try:
        from notifications.services import notification_service, normalize_whatsapp_phone

        digits, phone_err = normalize_whatsapp_phone(phone)
        if phone_err:
            logger.warning(
                "incident_assignee_notify: bad phone for user %s: %s",
                getattr(user, "id", None),
                phone_err,
            )
            return False

        token = getattr(settings, "WHATSAPP_ACCESS_TOKEN", None)
        phone_id = getattr(settings, "WHATSAPP_PHONE_NUMBER_ID", None)
        if not token or not phone_id:
            logger.info("incident_assignee_notify: WhatsApp not configured, skip")
            return False

        ok, meta = notification_service.send_whatsapp_text(digits, body)
        if not ok:
            logger.warning(
                "incident_assignee_notify: WhatsApp send failed for %s: %s",
                getattr(user, "id", None),
                meta,
            )
        return bool(ok)
    except Exception:
        logger.exception(
            "incident_assignee_notify: failed for user %s",
            getattr(user, "id", None),
        )
        return False


def notify_assignee_whatsapp_for_incident(ticket: "SafetyConcernReport") -> None:
    """
    Send WhatsApp to ticket.assigned_to, plus any other category owners as informed.
    No-op when WhatsApp is not configured.
    """
    assignee = getattr(ticket, "assigned_to", None)
    restaurant = getattr(ticket, "restaurant", None)

    notify_users: list = []
    seen: set[str] = set()
    if assignee:
        if not (ticket.reporter_id and assignee.id == ticket.reporter_id):
            notify_users.append(assignee)
            seen.add(str(assignee.id))

    if restaurant is not None:
        try:
            from staff.incident_routing import resolve_all_assignees_for_incident_type

            for owner in resolve_all_assignees_for_incident_type(
                restaurant, ticket.incident_type
            ):
                oid = str(owner.id)
                if oid in seen:
                    continue
                if ticket.reporter_id and owner.id == ticket.reporter_id:
                    continue
                seen.add(oid)
                notify_users.append(owner)
        except Exception:
            logger.exception("incident_assignee_notify: resolve_all failed")

    for user in notify_users:
        is_primary = assignee is not None and str(user.id) == str(assignee.id)
        body = _build_assignee_message(ticket, informed=not is_primary)
        _send_whatsapp(user, body)


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
