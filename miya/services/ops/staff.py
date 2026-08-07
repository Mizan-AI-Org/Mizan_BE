"""Find staff — tenant-scoped, permission-checked."""
from __future__ import annotations

import re
from typing import Any

from django.db.models import Q

from miya.services.ops.context import OpsContext, require_permission, require_restaurant
from miya.services.ops.result import OpsResult, fail, ok


def _serialize_staff(u) -> dict[str, Any]:
    name = f"{(u.first_name or '').strip()} {(u.last_name or '').strip()}".strip() or u.email
    tags = []
    try:
        profile = getattr(u, "profile", None)
        if profile and getattr(profile, "tags", None):
            tags = list(profile.tags or [])
    except Exception:
        tags = []
    return {
        "id": str(u.id),
        "name": name,
        "role": u.role,
        "email": u.email or "",
        "phone": getattr(u, "phone", "") or "",
        "tags": tags,
    }


def find_staff(
    ctx: OpsContext,
    *,
    name: str = "",
    role: str = "",
    tag: str = "",
    q: str = "",
    limit: int = 20,
) -> OpsResult:
    from miya.services.ops.context import require_establishment_context

    err = require_restaurant(ctx) or require_permission(ctx, "miya_full_tools")
    if err:
        return err
    est_err = require_establishment_context(ctx, for_action="staff")
    if est_err:
        return est_err

    from accounts.models import CustomUser

    qs = CustomUser.objects.filter(restaurant=ctx.restaurant, is_active=True).exclude(
        role__in=("SUPER_ADMIN", "PLATFORM_ADMIN")
    )
    # Never return staff portfolios outside the active / visible establishments
    if ctx.location_id:
        qs = qs.filter(
            Q(primary_location_id=ctx.location_id)
            | Q(allowed_locations__id=ctx.location_id)
            | Q(managed_locations__id=ctx.location_id)
        ).distinct()
    elif len(ctx.available_locations) > 1:
        visible_ids = [r["id"] for r in ctx.available_locations if r.get("id")]
        qs = qs.filter(
            Q(primary_location_id__in=visible_ids)
            | Q(allowed_locations__id__in=visible_ids)
            | Q(managed_locations__id__in=visible_ids)
            | Q(primary_location_id__isnull=True)  # unscoped staff still tenant-visible
        ).distinct()

    needle = (name or q or "").strip()
    role_f = (role or "").strip().upper()
    tag_f = (tag or "").strip().upper()

    # Kitchen / bar / service shorthand → tags or role filter
    if needle and not role_f and not tag_f:
        low = needle.lower()
        if low in ("kitchen", "cuisine", "cook", "chef"):
            tag_f = "KITCHEN"
            needle = ""
        elif low in ("bar", "bartender"):
            tag_f = "BAR"
            needle = ""
        elif low in ("service", "floor", "waiter", "waiters"):
            tag_f = "SERVICE"
            needle = ""

    if role_f:
        qs = qs.filter(role__iexact=role_f)
    if tag_f:
        qs = qs.filter(profile__tags__contains=[tag_f])
    if needle:
        tokens = [t for t in re.split(r"\s+", needle) if t]
        for tok in tokens:
            qs = qs.filter(
                Q(first_name__icontains=tok)
                | Q(last_name__icontains=tok)
                | Q(email__icontains=tok)
            )

    rows = [_serialize_staff(u) for u in qs.order_by("first_name", "last_name")[: max(1, min(limit, 40))]]
    if not rows:
        return fail(
            code="staff_not_found",
            message=(
                f"I couldn't find anyone matching '{name or q or role or tag}'."
                if (name or q or role or tag)
                else "No staff found in this workspace."
            ),
            miya_directive="Ask for a clearer name or list_staff without filters.",
        )

    label = name or q or role or tag or "staff"
    return ok(
        message=f"Found {len(rows)} staff match(es) for '{label}'.",
        verified=True,
        data={"count": len(rows), "staff": rows},
    )
