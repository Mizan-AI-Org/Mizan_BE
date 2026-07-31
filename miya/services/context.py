"""Build Miya system prompt and session context from the authenticated user."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from accounts.views_agent import (
    _effective_business_vertical,
    _miya_vertical_runtime_note,
)
from accounts.vertical_playbooks import vertical_playbook_for_api
from miya.persona import MIYA_SUPER_AGENT_PERSONA, channel_runtime_note
from miya.services.tenant import (
    resolve_active_tenant,
    tenant_context_note,
    user_tenant_memberships,
)


def build_session_context(
    user,
    *,
    channel: str = "dashboard",
    preferred_restaurant_id: str | None = None,
    session_hint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    hint = dict(session_hint or {})
    if preferred_restaurant_id and not hint.get("restaurant_id"):
        hint["restaurant_id"] = preferred_restaurant_id

    restaurant = resolve_active_tenant(
        user,
        preferred_restaurant_id=preferred_restaurant_id,
        session_hint=hint,
    )
    memberships = user_tenant_memberships(user)
    restaurant_id = str(restaurant.id) if restaurant else None
    restaurant_name = getattr(restaurant, "name", None) or "Unknown"
    business_vertical = _effective_business_vertical(restaurant)
    tz = ZoneInfo(getattr(restaurant, "timezone", None) or "UTC")
    now = datetime.now(tz)

    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or user.email
    phone = "".join(filter(str.isdigit, str(getattr(user, "phone", None) or "")))

    return {
        "user_id": str(user.id),
        "user_name": full_name,
        "user_email": user.email,
        "user_phone": phone,
        "role": user.role,
        "tenant_role": (
            next(
                (m["role"] for m in memberships if m["restaurant_id"] == restaurant_id),
                user.role,
            )
            if restaurant_id
            else user.role
        ),
        "restaurant_id": restaurant_id,
        "restaurant_name": restaurant_name,
        "tenant_memberships": memberships,
        "business_vertical": business_vertical,
        "vertical_playbook": vertical_playbook_for_api(business_vertical),
        "local_time": now.strftime("%Y-%m-%d %H:%M %Z"),
        "timezone": str(tz),
        "channel": (channel or "dashboard").strip().lower(),
    }


def build_system_prompt(
    user,
    *,
    channel: str = "dashboard",
    preferred_restaurant_id: str | None = None,
    session_hint: dict[str, Any] | None = None,
) -> str:
    ctx = build_session_context(
        user,
        channel=channel,
        preferred_restaurant_id=preferred_restaurant_id,
        session_hint=session_hint,
    )
    vertical_note = _miya_vertical_runtime_note(ctx["business_vertical"])
    channel_note = channel_runtime_note(ctx["channel"])

    persistent = (
        f"\n[SYSTEM: PERSISTENT CONTEXT]\n"
        f"Workspace: {ctx['restaurant_name']} (restaurant_id / tenant: {ctx['restaurant_id']})\n"
        f"User: {ctx['user_name']} (user_id: {ctx['user_id']})\n"
        f"Role: {ctx['role']}\n"
        f"Phone: {ctx['user_phone'] or 'unknown'}\n"
        f"business_vertical: {ctx['business_vertical']}\n"
        f"Current time: {ctx['local_time']} ({ctx['timezone']})\n"
        f"Channel: {ctx['channel']}\n"
        f"Voice: Fish Audio TTS when voice mode is on. Keep speakable replies concise.\n"
        f"Shared WhatsApp number: +212784476751 (identity from phone → tenant + RBAC).\n"
        + tenant_context_note(ctx.get("tenant_memberships") or [], ctx.get("restaurant_id"))
    )

    return (
        MIYA_SUPER_AGENT_PERSONA
        + "\n"
        + channel_note
        + vertical_note
        + persistent
    )
