"""Tenant (workspace) resolution and membership checks for Miya."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _norm_rid(val) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    return s or None


def user_tenant_memberships(user) -> list[dict[str, Any]]:
    """
    All workspaces this user belongs to, with role at each.
    Sources: primary ``CustomUser.restaurant``, ``StaffRestaurantLink``, ``UserRole``.
    """
    if not user or not getattr(user, "is_authenticated", True):
        return []

    from accounts.models import Restaurant, StaffRestaurantLink

    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    def _add(restaurant, role: str, *, is_primary: bool, source: str) -> None:
        if not restaurant:
            return
        rid = str(restaurant.id)
        if rid in seen:
            return
        seen.add(rid)
        out.append(
            {
                "restaurant_id": rid,
                "restaurant_name": getattr(restaurant, "name", None) or "Workspace",
                "role": (role or getattr(user, "role", "") or "").upper(),
                "is_primary": is_primary,
                "source": source,
            }
        )

    primary_rest = getattr(user, "restaurant", None)
    if primary_rest:
        _add(
            primary_rest,
            getattr(user, "role", "") or "",
            is_primary=True,
            source="primary",
        )

    try:
        for link in StaffRestaurantLink.objects.filter(
            user=user, is_active=True
        ).select_related("restaurant"):
            _add(
                link.restaurant,
                link.role or getattr(user, "role", "") or "",
                is_primary=False,
                source="staff_link",
            )
    except Exception:
        logger.debug("StaffRestaurantLink lookup failed for user %s", getattr(user, "id", None))

    try:
        for ur in user.restaurant_roles.select_related("restaurant", "role").all():
            role_name = getattr(getattr(ur, "role", None), "name", None) or getattr(
                user, "role", ""
            )
            _add(
                ur.restaurant,
                role_name,
                is_primary=bool(getattr(ur, "is_primary", False)),
                source="user_role",
            )
    except Exception:
        logger.debug("restaurant_roles lookup failed for user %s", getattr(user, "id", None))

    out.sort(key=lambda m: (not m.get("is_primary"), m.get("restaurant_name") or ""))
    return out


def user_can_access_tenant(user, restaurant_id) -> bool:
    rid = _norm_rid(restaurant_id)
    if not user or not rid:
        return False
    return any(m["restaurant_id"] == rid for m in user_tenant_memberships(user))


def effective_role_at_tenant(user, restaurant) -> str:
    """Role string for ``user`` at ``restaurant`` (falls back to ``user.role``)."""
    if not user:
        return ""
    rid = str(getattr(restaurant, "id", restaurant or ""))
    for m in user_tenant_memberships(user):
        if m["restaurant_id"] == rid:
            return (m.get("role") or "").upper()
    return (getattr(user, "role", "") or "").upper()


def resolve_active_tenant(
    user,
    *,
    preferred_restaurant_id: str | None = None,
    session_hint: dict[str, Any] | None = None,
):
    """
    Pick the workspace Miya should act in for this user.
    Priority: explicit preferred id (if member) → session tenant → primary FK → primary role → first membership.
    """
    from accounts.models import Restaurant

    if not user:
        return None

    memberships = user_tenant_memberships(user)
    if not memberships:
        return getattr(user, "restaurant", None)

    preferred = _norm_rid(preferred_restaurant_id)
    if preferred:
        if user_can_access_tenant(user, preferred):
            try:
                return Restaurant.objects.get(id=preferred)
            except Restaurant.DoesNotExist:
                pass
        logger.warning(
            "Miya tenant: user %s cannot access preferred restaurant %s",
            getattr(user, "id", None),
            preferred,
        )

    hint = session_hint or {}
    hinted = _norm_rid(hint.get("tenant_id") or hint.get("restaurant_id"))
    if hinted and user_can_access_tenant(user, hinted):
        try:
            return Restaurant.objects.get(id=hinted)
        except Restaurant.DoesNotExist:
            pass

    for m in memberships:
        if m.get("is_primary"):
            try:
                return Restaurant.objects.get(id=m["restaurant_id"])
            except Restaurant.DoesNotExist:
                break

    if getattr(user, "restaurant_id", None):
        return getattr(user, "restaurant", None)

    try:
        return Restaurant.objects.get(id=memberships[0]["restaurant_id"])
    except Exception:
        return None


def bind_tool_payload_to_tenant(
    user,
    payload: dict[str, Any],
    session_context: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """
    Force ``restaurant_id`` on agent payloads to the resolved tenant.
    Returns (payload, error_dict) — error_dict when tenant cannot be resolved.
    """
    payload = dict(payload or {})
    ctx = dict(session_context or {})
    tenant_id = _norm_rid(ctx.get("restaurant_id"))

    arg_rid = _norm_rid(
        payload.get("restaurant_id")
        or payload.get("restaurantId")
        or payload.get("restaurant")
    )
    if arg_rid and tenant_id and arg_rid != tenant_id:
        if user_can_access_tenant(user, arg_rid):
            tenant_id = arg_rid
            ctx["restaurant_id"] = arg_rid
        else:
            return payload, {
                "success": False,
                "error": "That workspace is not linked to this account.",
                "required_rbac": False,
            }

    if arg_rid and not tenant_id and user_can_access_tenant(user, arg_rid):
        tenant_id = arg_rid
        ctx["restaurant_id"] = arg_rid

    if not tenant_id:
        rest = resolve_active_tenant(user, session_hint=ctx)
        if rest:
            tenant_id = str(rest.id)
            ctx["restaurant_id"] = tenant_id

    if not tenant_id:
        return payload, {
            "success": False,
            "error": (
                "Unable to determine your workspace (tenant). "
                "Ensure your account is linked to a Mizan workspace."
            ),
        }

    payload["restaurant_id"] = tenant_id
    payload.setdefault("restaurantId", tenant_id)
    ctx["restaurant_id"] = tenant_id
    if session_context is not None:
        session_context["restaurant_id"] = tenant_id
    return payload, None


def tenant_context_note(memberships: list[dict[str, Any]], active_id: str | None) -> str:
    if not memberships:
        return (
            "\n[TENANT] No workspace membership on file — escalate if tools fail.\n"
        )
    lines = [
        "\n[TENANT MEMBERSHIP — always scope tools to active workspace]",
        f"Active tenant (restaurant_id): {active_id or 'unknown'}",
        "Every tool call MUST include this restaurant_id. Never cross tenants.",
    ]
    if len(memberships) > 1:
        lines.append("This user belongs to multiple workspaces:")
        for m in memberships:
            mark = " ← ACTIVE" if m["restaurant_id"] == active_id else ""
            lines.append(
                f"  · {m['restaurant_name']} (id={m['restaurant_id']}, role={m['role']}){mark}"
            )
    else:
        m = memberships[0]
        lines.append(
            f"Single workspace: {m['restaurant_name']} (role={m['role']})."
        )
    lines.append(
        "Staff lookup, shifts, tasks, inventory, and announcements are ONLY for this tenant.\n"
    )
    return "\n".join(lines) + "\n"
