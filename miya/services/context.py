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
from core.i18n import get_effective_language, normalize_language
from miya.persona import MIYA_SUPER_AGENT_PERSONA, channel_runtime_note
from miya.services.tenant_snapshot import build_tenant_snapshot_block
from miya.services.tenant import (
    resolve_active_tenant,
    tenant_context_note,
    user_tenant_memberships,
)

_LANGUAGE_LABELS = {
    "en": "English",
    "fr": "French",
    "ar": (
        "Arabic (Modern Standard Arabic by default; use Darija / Moroccan Arabic "
        "when the user writes Maghrebi dialect)"
    ),
}


def reply_language_label(lang: str) -> str:
    code = normalize_language(lang)
    return _LANGUAGE_LABELS.get(code, "English")


def reply_language_block(lang: str) -> str:
    """Hard directive so Miya doesn't default to English when the user message is ambiguous."""
    code = normalize_language(lang)
    label = reply_language_label(code)
    return (
        f"\n[REPLY LANGUAGE]\n"
        f"Default reply language for this user/workspace: {label} (code: {code}).\n"
        f"- Write EVERY reply in {label} unless the user clearly writes a full message "
        f"in another supported language.\n"
        f"- Short acknowledgements (ok, merci, 👍, تم) are NOT a language switch — "
        f"stay in {label}.\n"
        f"- Gibberish, typos, unknown commands, or English tool/API jargon must still "
        f"get a {label} reply.\n"
        f"- Never answer in English by default when the reply language is {label}.\n"
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
    language = get_effective_language(user=user, restaurant=restaurant)

    return {
        "user_id": str(user.id),
        "user_name": full_name,
        "user_email": user.email,
        "user_phone": phone,
        "role": user.role,
        "language": language,
        "thread_id": hint.get("thread_id") or hint.get("whatsapp_session_id"),
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
    language = ctx.get("language") or "en"
    language_note = reply_language_block(language)

    persistent = (
        f"\n[SYSTEM: PERSISTENT CONTEXT]\n"
        f"Workspace: {ctx['restaurant_name']} (restaurant_id / tenant: {ctx['restaurant_id']})\n"
        f"User: {ctx['user_name']} (user_id: {ctx['user_id']})\n"
        f"Role: {ctx['role']}\n"
        f"Preferred language: {reply_language_label(language)} ({language})\n"
        f"Phone: {ctx['user_phone'] or 'unknown'}\n"
        f"business_vertical: {ctx['business_vertical']}\n"
        f"Current time: {ctx['local_time']} ({ctx['timezone']})\n"
        f"Channel: {ctx['channel']}\n"
        f"Voice: Fish Audio TTS when voice mode is on. Keep speakable replies concise.\n"
        f"Shared WhatsApp number: +212784476751 (identity from phone → tenant + RBAC).\n"
        + tenant_context_note(ctx.get("tenant_memberships") or [], ctx.get("restaurant_id"))
    )

    snapshot = ""
    if ctx.get("restaurant_id"):
        from accounts.models import Restaurant

        restaurant = Restaurant.objects.filter(id=ctx["restaurant_id"]).first()
        snapshot = build_tenant_snapshot_block(restaurant)

    return (
        MIYA_SUPER_AGENT_PERSONA
        + "\n"
        + channel_note
        + language_note
        + vertical_note
        + persistent
        + snapshot
    )
