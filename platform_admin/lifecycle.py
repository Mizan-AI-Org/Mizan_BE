"""Tenant lifecycle flags stored on ``Restaurant.general_settings``.

Serializer badges and list filters must use the same truthiness rules so a
tenant never appears as Suspended in the UI while still matching the Active
filter (Postgres JSON lookups miss some truthy shapes).
"""
from __future__ import annotations

from typing import Any


def flag_truthy(value: Any) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def tenant_is_suspended(general_settings: dict | None) -> bool:
    gs = general_settings or {}
    return flag_truthy(gs.get("platform_suspended"))


def tenant_is_deactivated(general_settings: dict | None) -> bool:
    gs = general_settings or {}
    return flag_truthy(gs.get("platform_deactivated"))


def tenant_lifecycle(general_settings: dict | None) -> str:
    """Return ``deactivated``, ``suspended``, or ``active`` (priority order)."""
    if tenant_is_deactivated(general_settings):
        return "deactivated"
    if tenant_is_suspended(general_settings):
        return "suspended"
    return "active"


def restaurant_access_denied_reason(restaurant) -> str | None:
    """Return a user-facing error if this tenant must not use the product."""
    if restaurant is None:
        return None
    gs = getattr(restaurant, "general_settings", None) or {}
    if tenant_is_deactivated(gs):
        return (
            "This business account has been deactivated. "
            "Contact Mizan support if you believe this is a mistake."
        )
    if tenant_is_suspended(gs):
        return (
            "This business account has been suspended. "
            "Contact Mizan support if you believe this is a mistake."
        )
    return None


def user_tenant_access_denied_reason(user) -> str | None:
    """Return a user-facing error if this account must not use tenant apps.

    Platform operators are exempt (they sign in at ``/admin``).
    """
    if not user:
        return "Invalid account."

    # Ops accounts authenticate via /admin — never blocked by tenant flags here.
    try:
        from platform_admin.permissions import user_is_platform_ops_account

        if user_is_platform_ops_account(user):
            return None
    except Exception:
        if getattr(user, "is_platform_operator", False):
            return None

    if not getattr(user, "is_active", False):
        return "This account has been deactivated. Contact your manager or support."

    return restaurant_access_denied_reason(getattr(user, "restaurant", None))
