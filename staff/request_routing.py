"""
Default assignee resolution for :class:`staff.StaffRequest`.

Reads ``Restaurant.general_settings['category_owners']`` — the mapping
produced by onboarding step 4 (and editable in Settings). Each key is a
slug like ``request.hr`` / ``incident.equipment`` / ``task.foh`` and the
value is the CustomUser UUID responsible for that bucket.

This module is deliberately separate from :mod:`staff.incident_routing`
because the two features use different storage keys and different
canonical vocabularies. Share code only via the tiny
``_lookup_user_by_id`` helper below.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Iterable, Optional

if TYPE_CHECKING:
    from accounts.models import CustomUser, Restaurant

logger = logging.getLogger(__name__)


# StaffRequest.category -> ordered list of onboarding slugs we'll try when
# resolving the default owner. First match wins. Kept permissive so
# existing tenants (who may only have the older ``incident.*`` slugs
# configured) still get routed sensibly.
_CATEGORY_TO_SLUGS: dict[str, tuple[str, ...]] = {
    "DOCUMENT": ("request.document",),
    "HR": ("request.hr", "incident.hr"),
    "SCHEDULING": ("request.scheduling",),
    "PAYROLL": ("request.payroll", "request.hr"),
    "OPERATIONS": ("task.foh", "task.boh", "task.bar"),
    "MAINTENANCE": ("request.maintenance", "incident.equipment"),
    "RESERVATIONS": ("request.reservations",),
    "INVENTORY": ("request.inventory",),
    # Procurement asks ("buy 6 bottles of vodka"). Most kitchens give
    # this responsibility to whoever owns inventory, so we fall back
    # to ``request.inventory`` when the dedicated slug isn't set —
    # that way existing tenants get sensible routing without having
    # to revisit onboarding.
    "PURCHASE_ORDER": ("request.purchase_order", "request.inventory"),
    "FINANCE": ("request.finance", "task.finance"),
    "MEDICAL": ("request.medical", "request.hr"),
    "OTHER": (),
}


# Public alias — other modules (e.g. onboarding wizard) import this so the
# allowed slug list stays in one place.
ALL_CATEGORY_OWNER_SLUGS: tuple[str, ...] = tuple(
    sorted({slug for slugs in _CATEGORY_TO_SLUGS.values() for slug in slugs})
)


def slugs_for_category(category: Optional[str]) -> tuple[str, ...]:
    """Return the lookup slugs for a StaffRequest.category value."""
    if not category:
        return ()
    return _CATEGORY_TO_SLUGS.get(str(category).upper(), ())


def _lookup_user_by_id(
    restaurant: "Restaurant",
    user_id: str,
) -> Optional["CustomUser"]:
    from accounts.models import CustomUser  # local import to avoid cycles

    try:
        return CustomUser.objects.get(
            id=user_id,
            restaurant_id=restaurant.id,
            is_active=True,
        )
    except (CustomUser.DoesNotExist, ValueError, TypeError) as exc:
        logger.warning(
            "category_owners: invalid user id %s for restaurant %s: %s",
            user_id,
            getattr(restaurant, "id", None),
            exc,
        )
        return None


def _uids_from_raw(raw) -> list[str]:
    """Normalize a mapping value (string or list) into ordered UUID strings."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, (list, tuple)):
        out: list[str] = []
        for item in raw:
            out.extend(_uids_from_raw(item))
        return out
    uid = str(raw).strip()
    return [uid] if uid else []


def _first_uid(mapping: dict, slugs: Iterable[str]) -> Optional[str]:
    """Return the first non-empty UUID in ``mapping`` for any of ``slugs``.

    Onboarding stores owners as ``string[]`` per slug; agent API may store a
    single string. Accept both. Kept for backward-compatible single-assignee
    resolution.
    """
    uids = _all_uids(mapping, slugs)
    return uids[0] if uids else None


def _all_uids(mapping: dict, slugs: Iterable[str]) -> list[str]:
    """Return all non-empty UUIDs in ``mapping`` for any of ``slugs``.

    Preserves configured order, de-duplicates. Walks slugs in priority order
    so owners on the primary slug appear before fallback-slug owners.
    """
    seen: set[str] = set()
    out: list[str] = []

    def _consume(raw) -> None:
        for uid in _uids_from_raw(raw):
            if uid not in seen:
                seen.add(uid)
                out.append(uid)

    for slug in slugs:
        if slug in mapping:
            _consume(mapping.get(slug))
    # Case-insensitive fallback: handles manually-edited JSON.
    lowered = {str(k).lower(): v for k, v in mapping.items() if isinstance(k, str)}
    for slug in slugs:
        key = slug.lower()
        if key in lowered and slug not in mapping:
            _consume(lowered.get(key))
    return out


def resolve_default_assignee_for_category(
    restaurant: Optional["Restaurant"],
    category: Optional[str],
) -> Optional["CustomUser"]:
    """
    Return the primary CustomUser that should own a new StaffRequest in this
    category, or ``None`` if no owner is configured.

    Equivalent to the first entry of
    :func:`resolve_all_assignees_for_category` (backward-compatible).
    """
    owners = resolve_all_assignees_for_category(restaurant, category)
    return owners[0] if owners else None


def resolve_all_assignees_for_category(
    restaurant: Optional["Restaurant"],
    category: Optional[str],
) -> list["CustomUser"]:
    """
    Return ALL active CustomUsers that own this category bucket.

    Resolution order:

    1. ``restaurant.general_settings['category_owners']`` — every UUID
       listed under the category's slugs (onboarding stores ``string[]``).
    2. **Tag-based fallback** — if no explicit owners are configured,
       return every active staff member tagged for this category
       (see :data:`accounts.staff_tags.CATEGORY_TAGS`), ordered by role
       priority then name.

    Primary assignee for the StaffRequest FK is ``result[0]`` when present;
    remaining owners should still be notified as informed.
    """
    if not restaurant:
        return []

    # 1) Explicit ``category_owners`` mapping — fan out to every configured owner.
    gs = restaurant.general_settings or {}
    mapping = gs.get("category_owners") or {}
    if isinstance(mapping, dict) and mapping:
        slugs = slugs_for_category(category)
        if slugs:
            users: list["CustomUser"] = []
            seen_ids: set[str] = set()
            for uid in _all_uids(mapping, slugs):
                user = _lookup_user_by_id(restaurant, uid)
                if user is None:
                    continue
                sid = str(user.id)
                if sid in seen_ids:
                    continue
                seen_ids.add(sid)
                users.append(user)
            if users:
                return users

    # 2) Tag-based fallback — all tagged candidates for the first matching tag.
    return _resolve_all_assignees_by_tag(restaurant, category)


def _resolve_all_assignees_by_tag(
    restaurant: "Restaurant",
    category: Optional[str],
) -> list["CustomUser"]:
    """Return active staff tagged for this category (stable role/name order)."""
    if not category:
        return []

    from accounts.staff_tags import tags_for_category  # local import: avoid cycles
    from accounts.models import CustomUser

    tags = tags_for_category(category)
    if not tags:
        return []

    role_priority = {"OWNER": 0, "ADMIN": 1, "MANAGER": 2}

    rid = getattr(restaurant, "id", None)
    if rid is None:
        return []

    for tag in tags:
        candidates = list(
            CustomUser.objects.filter(
                restaurant_id=rid,
                is_active=True,
                profile__tags__contains=[tag],
            )
            .exclude(role="SUPER_ADMIN")
            .select_related("profile")
        )
        if not candidates:
            continue
        candidates.sort(
            key=lambda u: (
                role_priority.get((u.role or "").upper(), 99),
                (u.first_name or "").lower(),
                (u.last_name or "").lower(),
                str(u.id),
            )
        )
        return candidates

    return []
