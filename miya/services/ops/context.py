"""Resolved operational context for canonical Miya ops services."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class OpsContext:
    user: Any
    restaurant: Any
    restaurant_id: str
    user_id: str
    role: str
    channel: str = "dashboard"
    language: str = "en"
    location_id: str | None = None
    location_name: str | None = None
    available_locations: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_session(
        cls,
        *,
        user,
        restaurant,
        session_context: dict[str, Any] | None = None,
    ) -> "OpsContext":
        from miya.services.ops.scoping import (
            serialize_location,
            user_can_access_location,
            visible_locations_for_user,
        )

        ctx = session_context or {}
        rid = str(
            getattr(restaurant, "id", None)
            or ctx.get("restaurant_id")
            or ""
        ).strip()
        uid = str(getattr(user, "id", None) or ctx.get("user_id") or "").strip()
        role = str(ctx.get("role") or getattr(user, "role", "") or "").upper()

        visible = visible_locations_for_user(user, restaurant)
        available = [serialize_location(L) for L in visible]

        loc_id = str(ctx.get("location_id") or "").strip() or None
        loc_name = str(ctx.get("location_name") or "").strip() or None

        # Reject inaccessible sticky location (no privilege escalation)
        if loc_id and not user_can_access_location(user, restaurant, loc_id):
            loc_id = None
            loc_name = None

        if not loc_id and loc_name:
            from miya.services.ops.scoping import resolve_location_by_name

            loc, _ = resolve_location_by_name(
                restaurant,
                loc_name,
                visible=visible,
            )
            if loc:
                loc_id = str(loc.id)
                loc_name = loc.name

        # Auto-bind when only one establishment is visible
        if not loc_id and len(visible) == 1:
            loc_id = str(visible[0].id)
            loc_name = visible[0].name

        if loc_id and not loc_name:
            for row in available:
                if row["id"] == loc_id:
                    loc_name = row["name"]
                    break

        return cls(
            user=user,
            restaurant=restaurant,
            restaurant_id=rid,
            user_id=uid,
            role=role,
            channel=str(ctx.get("channel") or "dashboard").lower(),
            language=str(ctx.get("language") or "en"),
            location_id=loc_id,
            location_name=loc_name,
            available_locations=available,
        )


def require_restaurant(ctx: OpsContext):
    from miya.services.ops.result import fail

    if not ctx.restaurant or not ctx.restaurant_id:
        return fail(
            code="restaurant_required",
            message="I couldn't determine which workspace this is for.",
            miya_directive="Ask which workspace if the user has several.",
        )
    return None


def require_establishment_context(ctx: OpsContext, *, for_action: str = "this"):
    """
    When the user can see multiple establishments and none is active,
    ask which one — never return cross-branch aggregates silently.
    """
    from miya.services.ops.result import clarify

    err = require_restaurant(ctx)
    if err:
        return err
    if ctx.location_id:
        return None
    if len(ctx.available_locations) <= 1:
        return None
    names = ", ".join(r["name"] for r in ctx.available_locations[:8])
    return clarify(
        message=(
            f"Which establishment do you mean for {for_action}? "
            f"You have access to: {names}."
        ),
        data={
            "establishments": ctx.available_locations,
            "needs_establishment": True,
            "count": len(ctx.available_locations),
        },
        code="needs_establishment",
    )


def assert_location_access(ctx: OpsContext, location_id: str | None):
    from miya.services.ops.result import fail
    from miya.services.ops.scoping import user_can_access_location

    if not location_id:
        return None
    if user_can_access_location(ctx.user, ctx.restaurant, location_id):
        return None
    return fail(
        code="location_forbidden",
        message="You don't have access to that establishment.",
        miya_directive="Do not reveal data from establishments the user cannot access.",
    )


def require_permission(ctx: OpsContext, action_id: str | None):
    from accounts.rbac_enforce import miya_has_full_tenant_access, user_can_action
    from miya.services.ops.result import fail

    if action_id is None:
        return None
    if miya_has_full_tenant_access(ctx.user, ctx.restaurant):
        return None
    if user_can_action(ctx.user, action_id, restaurant=ctx.restaurant):
        return None
    return fail(
        code="permission_denied",
        message="You don't have permission to do that in this workspace.",
        miya_directive="Do not claim the action succeeded. Offer to ask a manager.",
    )


def user_is_task_assignee(task, user) -> bool:
    """True if user is primary assignee or on M2M assignees."""
    if not task or not user or not getattr(user, "pk", None):
        return False
    uid = getattr(user, "id", None)
    if getattr(task, "assigned_to_id", None) == uid:
        return True
    if getattr(task, "assignee_id", None) == uid:
        return True
    if getattr(task, "staff_id", None) == uid:
        return True
    try:
        return task.assignees.filter(id=uid).exists()
    except Exception:
        return False


def user_can_read_task(ctx: OpsContext, task) -> bool:
    """Managers may read any tenant task; staff may read tasks assigned to them."""
    from accounts.rbac_enforce import miya_has_full_tenant_access, user_can_action

    if miya_has_full_tenant_access(ctx.user, ctx.restaurant):
        return True
    if user_can_action(ctx.user, "manage_widgets", restaurant=ctx.restaurant):
        return True
    return user_is_task_assignee(task, ctx.user)


def require_task_status_permission(ctx: OpsContext, task):
    """
    Managers with manage_widgets may update any tenant task.
    Assignees may update their own task status (WhatsApp staff self-service).
    """
    from accounts.rbac_enforce import miya_has_full_tenant_access, user_can_action
    from miya.services.ops.result import fail

    if miya_has_full_tenant_access(ctx.user, ctx.restaurant):
        return None
    if user_can_action(ctx.user, "manage_widgets", restaurant=ctx.restaurant):
        return None
    if user_is_task_assignee(task, ctx.user):
        return None
    return fail(
        code="permission_denied",
        message="You can only update tasks assigned to you.",
        miya_directive="Do not claim the action succeeded.",
    )


def _normalize_location_id(raw) -> str | None:
    """Return a location id string or None — ignore unset/invalid/MagicMock values."""
    if raw is None:
        return None
    if isinstance(raw, (list, dict, tuple, set)):
        return None
    text = str(raw).strip()
    if not text or text in ("None", "[]") or text.startswith("<"):
        return None
    return text


def guard_entity_location(ctx: OpsContext, entity):
    """Fail closed if entity is tagged to an inaccessible or other active establishment."""
    from miya.services.ops.result import fail

    lid = _normalize_location_id(getattr(entity, "location_id", None)) or _normalize_location_id(
        getattr(entity, "business_location_id", None)
    )
    if not lid:
        return None
    denied = assert_location_access(ctx, str(lid))
    if denied:
        return denied
    if ctx.location_id and str(lid) != str(ctx.location_id):
        return fail(
            code="location_mismatch",
            message=(
                "That record belongs to another establishment. "
                "Switch context first (e.g. 'What about Casablanca?')."
            ),
            miya_directive="Do not reveal cross-establishment details.",
        )
    return None
