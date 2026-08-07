"""Establishment (BusinessLocation) visibility and queryset scoping — no cross-branch leakage."""
from __future__ import annotations

import re
from typing import Any

from django.db.models import Q, QuerySet


def _normalize_establishment_needle(name: str) -> str:
    """Collapse spaces/punctuation for fuzzy name match (e.g. zamazama → zama zama)."""
    return re.sub(r"[\s\-_'\.]+", "", (name or "").strip().lower())


def serialize_location(loc) -> dict[str, Any]:
    return {
        "id": str(loc.id),
        "name": loc.name,
        "is_primary": bool(getattr(loc, "is_primary", False)),
        "address": getattr(loc, "address", "") or "",
        "kind": "establishment",
    }


def visible_locations_for_user(user, restaurant) -> list:
    """
    Establishments the user may see within this tenant.
    - Owners/admins with empty managed_locations → all active branches
    - Managers with managed_locations → those only
    - Staff with allowed_locations → those only (else all if empty)
    """
    from accounts.models import BusinessLocation

    if restaurant is None:
        return []
    qs = BusinessLocation.objects.filter(restaurant=restaurant, is_active=True)
    if user is None:
        return list(qs.order_by("-is_primary", "name"))

    role = (getattr(user, "role", "") or "").upper()
    # Manager / owner portfolio scope
    if role in {"OWNER", "ADMIN", "SUPER_ADMIN", "MANAGER", "SUPERVISOR"}:
        try:
            managed = list(user.managed_locations.filter(restaurant=restaurant, is_active=True))
        except Exception:
            managed = []
        if managed:
            return sorted(managed, key=lambda L: (not L.is_primary, L.name or ""))
        return list(qs.order_by("-is_primary", "name"))

    # Staff: allowed_locations
    try:
        allowed = list(user.allowed_locations.filter(restaurant=restaurant, is_active=True))
    except Exception:
        allowed = []
    if allowed:
        return sorted(allowed, key=lambda L: (not L.is_primary, L.name or ""))
    # Empty allowed = all branches (legacy)
    return list(qs.order_by("-is_primary", "name"))


def user_can_access_location(user, restaurant, location_id: str | None) -> bool:
    if not location_id:
        return True
    lid = str(location_id).strip()
    for loc in visible_locations_for_user(user, restaurant):
        if str(loc.id) == lid:
            return True
    return False


def resolve_location_by_name(restaurant, name: str, *, visible: list | None = None):
    """Match establishment by name among visible list. Returns (loc|None, matches)."""
    needle = (name or "").strip().lower()
    if not needle:
        return None, []
    pool = visible if visible is not None else list(
        __import__("accounts.models", fromlist=["BusinessLocation"]).BusinessLocation.objects.filter(
            restaurant=restaurant, is_active=True
        )
    )
    exact = [L for L in pool if (L.name or "").strip().lower() == needle]
    if len(exact) == 1:
        return exact[0], exact
    if exact:
        return None, exact
    partial = [L for L in pool if needle in (L.name or "").lower()]
    if len(partial) == 1:
        return partial[0], partial
    collapsed = _normalize_establishment_needle(needle)
    if collapsed:
        collapsed_exact = [
            L for L in pool if _normalize_establishment_needle(L.name or "") == collapsed
        ]
        if len(collapsed_exact) == 1:
            return collapsed_exact[0], collapsed_exact
        if collapsed_exact:
            return None, collapsed_exact
        collapsed_partial = [
            L for L in pool if collapsed in _normalize_establishment_needle(L.name or "")
        ]
        if len(collapsed_partial) == 1:
            return collapsed_partial[0], collapsed_partial
        if collapsed_partial:
            return None, collapsed_partial
    return None, partial


def default_location_id(user, restaurant, visible: list | None = None) -> str | None:
    """Sticky default: primary_location if visible, else sole branch, else primary branch."""
    locs = visible if visible is not None else visible_locations_for_user(user, restaurant)
    if not locs:
        return None
    if len(locs) == 1:
        return str(locs[0].id)
    primary_id = getattr(user, "primary_location_id", None) if user else None
    if primary_id and any(str(L.id) == str(primary_id) for L in locs):
        return str(primary_id)
    for L in locs:
        if getattr(L, "is_primary", False):
            return str(L.id)
    return None


def apply_location_scope(
    qs: QuerySet,
    *,
    location_id: str | None,
    field: str = "location_id",
    allow_null: bool = False,
) -> QuerySet:
    """
    Restrict queryset to one establishment.
    When location_id is set: only that branch (optionally include null if allow_null).
    When unset: caller decides (usually require clarify for multi-branch).
    """
    if not location_id:
        return qs
    lid = str(location_id).strip()
    if allow_null:
        return qs.filter(Q(**{field: lid}) | Q(**{f"{field}__isnull": True}))
    return qs.filter(**{field: lid})


def filter_visible_location_ids(qs: QuerySet, *, location_ids: list[str], field: str = "location_id") -> QuerySet:
    """Restrict to any of the user's visible establishment ids (no other branches)."""
    if not location_ids:
        return qs.none()
    return qs.filter(**{f"{field}__in": location_ids})


def notification_data_with_location(data: dict | None, *, location_id: str | None = None, location_name: str | None = None) -> dict:
    """Stamp establishment onto notification.data for list scoping."""
    out = dict(data or {})
    if location_id:
        out["location_id"] = str(location_id)
    if location_name:
        out["location_name"] = location_name
    return out


def filter_notifications_by_location(qs: QuerySet, *, location_id: str | None, visible_ids: list[str] | None = None) -> QuerySet:
    """
    Scope notifications whose data.location_id is set.
    Unscoped notifications (no location_id in data) remain visible.
    """
    if location_id:
        lid = str(location_id)
        return qs.filter(Q(data__location_id=lid) | Q(data__location_id__isnull=True) | ~Q(data__has_key="location_id"))
    if visible_ids is not None and len(visible_ids) > 0:
        # Exclude notifications stamped to branches the user cannot see
        return qs.exclude(
            Q(data__has_key="location_id") & ~Q(data__location_id__in=[str(x) for x in visible_ids]) & ~Q(data__location_id="")
        )
    return qs
