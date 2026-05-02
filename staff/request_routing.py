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
    "PAYROLL": ("request.payroll", "task.finance"),
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


def _first_uid(mapping: dict, slugs: Iterable[str]) -> Optional[str]:
    """Return the first non-empty UUID in ``mapping`` for any of ``slugs``."""
    for slug in slugs:
        uid = mapping.get(slug)
        if uid:
            return str(uid)
    # Case-insensitive fallback: handles manually-edited JSON.
    lowered = {str(k).lower(): v for k, v in mapping.items() if isinstance(k, str)}
    for slug in slugs:
        uid = lowered.get(slug.lower())
        if uid:
            return str(uid)
    return None


def resolve_default_assignee_for_category(
    restaurant: Optional["Restaurant"],
    category: Optional[str],
) -> Optional["CustomUser"]:
    """
    Return the CustomUser that should own a new StaffRequest in this
    category, or ``None`` if no owner is configured.

    Resolution order (first match wins):

    1. ``restaurant.general_settings['category_owners']`` — the
       onboarding-step-4 mapping. This is the explicit, manager-curated
       answer; respect it when set.
    2. **Tag-based fallback** — if no explicit owner is configured,
       look up the canonical tag list for this category (see
       :data:`accounts.staff_tags.CATEGORY_TAGS`) and return the first
       active staff member who carries any of those tags. This means
       a fresh tenant who has assigned tags to staff but hasn't yet
       configured ``category_owners`` still gets requests routed
       sensibly — e.g. ``PURCHASE_ORDER`` lands on someone tagged
       ``PURCHASES``.

    Returns ``None`` only when neither path produces a candidate.
    """
    if not restaurant:
        return None

    # 1) Explicit ``category_owners`` mapping.
    gs = restaurant.general_settings or {}
    mapping = gs.get("category_owners") or {}
    if isinstance(mapping, dict) and mapping:
        slugs = slugs_for_category(category)
        if slugs:
            uid = _first_uid(mapping, slugs)
            if uid:
                user = _lookup_user_by_id(restaurant, uid)
                if user is not None:
                    return user

    # 2) Tag-based fallback.
    return _resolve_assignee_by_tag(restaurant, category)


def _resolve_assignee_by_tag(
    restaurant: "Restaurant",
    category: Optional[str],
) -> Optional["CustomUser"]:
    """Return the first active staff member tagged for this category.

    Walks the tag list from
    :data:`accounts.staff_tags.CATEGORY_TAGS` in declared order so the
    "primary" tag for a bucket (e.g. ``PURCHASES`` for
    ``PURCHASE_ORDER``) wins over secondary tags
    (``CONTROL`` / ``MANAGEMENT``). Within a single tag we order by
    ``role`` priority (Owner / Admin / Manager first) then alphabetic
    name so the result is stable across calls.
    """
    if not category:
        return None

    from accounts.staff_tags import tags_for_category  # local import: avoid cycles
    from accounts.models import CustomUser

    tags = tags_for_category(category)
    if not tags:
        return None

    # Authority tier order — high-authority owners get the bucket
    # first, so newly-onboarded teams don't accidentally route
    # PURCHASE_ORDER to a junior staff who happened to be tagged
    # PURCHASES alongside the buyer.
    role_priority = {"OWNER": 0, "ADMIN": 1, "MANAGER": 2}

    rid = getattr(restaurant, "id", None)
    if rid is None:
        return None

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
        return candidates[0]

    return None
