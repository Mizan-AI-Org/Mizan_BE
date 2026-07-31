"""Legacy HeyLua external agent — opt-in only. Mizan uses in-Django Miya by default."""

from __future__ import annotations

from django.conf import settings


def legacy_lua_enabled() -> bool:
    """True only when explicitly opting into the old external Lua / heylua.ai agent."""
    return bool(getattr(settings, "LUA_LEGACY_ENABLED", False))


def legacy_lua_whatsapp_url() -> str:
    if not legacy_lua_enabled():
        return ""
    return (getattr(settings, "LUA_WHATSAPP_WEBHOOK_URL", None) or "").strip()


def legacy_lua_user_events_configured() -> bool:
    if not legacy_lua_enabled():
        return False
    if getattr(settings, "LUA_USER_EVENTS_WEBHOOK", None):
        return True
    if getattr(settings, "LUA_AGENT_ID", None):
        return True
    return False
