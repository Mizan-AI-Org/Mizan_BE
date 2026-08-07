"""Deliver daily ops briefings with prefs + dedupe (no spam)."""
from __future__ import annotations

import logging
from typing import Any

from miya.services.intelligence.proactive.briefing import format_daily_briefing
from miya.services.intelligence.proactive.dedupe import mark_sent, should_send_briefing
from miya.services.intelligence.proactive.prefs import briefing_phone, can_deliver_now
from miya.services.intelligence.proactive.scanner import scan_daily_operations
from miya.services.intelligence.proactive.types import DailyBriefing

logger = logging.getLogger("miya.intelligence.proactive.delivery")


def build_and_maybe_deliver(
    *,
    restaurant,
    manager,
    period: str = "morning",
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Scan → format → gate prefs/quiet hours/dedupe → WhatsApp send.
    Returns delivery status dict.
    """
    briefing = scan_daily_operations(restaurant, user=manager, period=period)
    body = format_daily_briefing(
        briefing,
        manager_name=(
            f"{(getattr(manager, 'first_name', None) or '')} "
            f"{(getattr(manager, 'last_name', None) or '')}"
        ).strip()
        or getattr(manager, "email", "")
        or "",
    )
    uid = str(getattr(manager, "id", "") or "")

    ok_pref, pref_reason = can_deliver_now(manager)
    if not force and not ok_pref:
        return {
            "sent": False,
            "reason": pref_reason,
            "briefing": briefing.to_dict(),
            "body": body,
        }

    allow, why = should_send_briefing(briefing, user_id=uid, force=force)
    if not allow:
        # Still refresh handle context when we have items
        if briefing.items:
            from miya.services.intelligence.proactive.dedupe import store_briefing_context

            store_briefing_context(briefing, user_id=uid)
        return {
            "sent": False,
            "reason": why,
            "briefing": briefing.to_dict(),
            "body": body,
        }

    if dry_run:
        return {
            "sent": False,
            "reason": "dry_run",
            "briefing": briefing.to_dict(),
            "body": body,
        }

    phone = briefing_phone(manager)
    if not phone:
        return {"sent": False, "reason": "no_phone", "briefing": briefing.to_dict(), "body": body}

    try:
        from notifications.services import notification_service

        result = notification_service.send_whatsapp_text(phone, body)
        success = result[0] if isinstance(result, tuple) else bool(result)
    except Exception:
        logger.exception("daily ops briefing send failed user=%s", uid)
        return {"sent": False, "reason": "send_failed", "briefing": briefing.to_dict(), "body": body}

    if success:
        mark_sent(briefing, user_id=uid)
        return {"sent": True, "reason": why, "briefing": briefing.to_dict(), "body": body}
    return {"sent": False, "reason": "provider_rejected", "briefing": briefing.to_dict(), "body": body}


def on_demand_briefing(*, user, restaurant=None, period: str = "morning") -> dict[str, Any]:
    """Dashboard/Miya on-demand: always return text; optionally store context."""
    restaurant = restaurant or getattr(user, "restaurant", None)
    if restaurant is None:
        return {"reply": "I need your workspace to build today's briefing.", "success": False}
    briefing = scan_daily_operations(restaurant, user=user, period=period)
    from miya.services.intelligence.proactive.dedupe import store_briefing_context

    store_briefing_context(briefing, user_id=str(user.id))
    body = format_daily_briefing(
        briefing,
        manager_name=(getattr(user, "first_name", None) or "") or "",
    )
    return {
        "reply": body,
        "success": True,
        "briefing": briefing.to_dict(),
        "presentation_only": True,
        "assistant_text_is_not_executable": True,
    }
