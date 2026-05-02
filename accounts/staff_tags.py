"""
Canonical staff-tag vocabulary.

Each :class:`StaffProfile` carries a JSON ``tags`` array — a list of
short upper-snake-case identifiers describing the operational *context*
the person works in (KITCHEN, SERVICE, MARKETING, …). They are the
operational counterpart to ``CustomUser.role`` (which is the formal job
title, e.g. ``CHEF``): a single chef may carry tags ``KITCHEN`` and
``BACK_OFFICE``; a manager may carry ``MANAGEMENT`` and ``CONTROL``.

What tags unlock
----------------
* **Precise task assignment** — managers can filter staff by tag in the
  escalate / reassign picker and the schedule editor.
* **Smarter inbox routing** — when a ``StaffRequest`` arrives with a
  ``category`` (PURCHASE_ORDER, MAINTENANCE, FINANCE, …) and the
  restaurant doesn't have a ``category_owners`` mapping configured,
  ``staff.request_routing`` falls back to the first active staff
  member whose tag set matches the category (see ``CATEGORY_TAGS``).
* **Reporting** — dashboards can group hours / wages / requests by tag
  bucket without inventing a parallel taxonomy.

Why a constant + helpers, not a separate model
-----------------------------------------------
Tags are a small, slow-moving vocabulary that should be the same for
every tenant — onboarding a new restaurant should feel the same in
Casablanca and Bordeaux. A JSON column on ``StaffProfile`` keeps reads
zero-join-cost and keeps onboarding boring. If a tenant later needs
custom tags, we can add a ``Restaurant.custom_staff_tags`` JSON column
and merge it into :data:`canonical_tag_set` per request — without ever
touching the wire format used by clients today.
"""

from __future__ import annotations

from typing import Iterable


# The standardised vocabulary. Order matches the expected UI ordering
# (front-of-house → back-of-house → admin → support functions) so
# pickers render predictably across pages.
CANONICAL_STAFF_TAGS: tuple[str, ...] = (
    "KITCHEN",
    "SERVICE",
    "FRONT_OFFICE",
    "BACK_OFFICE",
    "PURCHASES",
    "CONTROL",
    "ADMINISTRATION",
    "MANAGEMENT",
    "HOUSEKEEPING",
    "MARKETING",
)


# Canonical set is exposed to validators as a frozenset so membership
# tests stay O(1).
CANONICAL_STAFF_TAG_SET: frozenset[str] = frozenset(CANONICAL_STAFF_TAGS)


# Display labels (English). Frontend should NOT use these directly —
# it has its own i18n strings keyed by the same upper-snake identifier.
# These are kept here so admin / shell scripts have a sane default.
STAFF_TAG_LABELS_EN: dict[str, str] = {
    "KITCHEN": "Kitchen",
    "SERVICE": "Service",
    "FRONT_OFFICE": "Front Office",
    "BACK_OFFICE": "Back Office",
    "PURCHASES": "Purchases",
    "CONTROL": "Control",
    "ADMINISTRATION": "Administration",
    "MANAGEMENT": "Management",
    "HOUSEKEEPING": "Housekeeping",
    "MARKETING": "Marketing",
}


# Map ``StaffRequest.category`` (from ``staff.intent_router``) to the
# tags that mark "this person is a sensible default owner for the
# bucket". The first match wins; multiple tags per category mean we
# search them in order. This is consulted by
# :func:`staff.request_routing.resolve_default_assignee_for_category`
# as a fallback when ``category_owners`` isn't configured.
CATEGORY_TAGS: dict[str, tuple[str, ...]] = {
    # People-and-paperwork buckets — admin / management own these.
    "DOCUMENT": ("ADMINISTRATION", "MANAGEMENT"),
    "HR": ("ADMINISTRATION", "MANAGEMENT"),
    "PAYROLL": ("ADMINISTRATION", "CONTROL", "MANAGEMENT"),
    # Scheduling lives with management (rotas / leave approvals).
    "SCHEDULING": ("MANAGEMENT", "ADMINISTRATION"),
    # Vendor invoices and treasury items belong to control / admin.
    "FINANCE": ("CONTROL", "ADMINISTRATION", "MANAGEMENT"),
    # Procurement explicitly owns a tag now — so "buy 6 bottles of
    # vodka" can land on the buyer / purchasing officer directly.
    "PURCHASE_ORDER": ("PURCHASES", "CONTROL", "MANAGEMENT"),
    # Stock observations: prefer purchases (they reorder), fall back
    # to kitchen (they hold stock) and back-office (operations).
    "INVENTORY": ("PURCHASES", "KITCHEN", "BACK_OFFICE"),
    # Maintenance lives in back-office in most restaurants.
    "MAINTENANCE": ("BACK_OFFICE", "MANAGEMENT"),
    # Reservations are a front-office responsibility.
    "RESERVATIONS": ("FRONT_OFFICE", "SERVICE", "MANAGEMENT"),
    # General operations — service first, then management.
    "OPERATIONS": ("SERVICE", "MANAGEMENT", "BACK_OFFICE"),
    # Meetings — calendar holders are usually management / admin.
    "MEETING": ("MANAGEMENT", "ADMINISTRATION"),
    # OTHER deliberately has no tag mapping — uncategorised work
    # should never auto-route.
}


def normalize_tag(value: object) -> str | None:
    """Return ``value`` as the canonical UPPER_SNAKE form, or ``None``.

    Accepts whatever the client sent (``"kitchen"``, ``"Front Office"``,
    ``" back office "``) and folds it to the canonical key. Returns
    ``None`` for empties so callers can drop noise easily.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # ``Front Office`` / ``front-office`` / ``front_office`` → ``FRONT_OFFICE``.
    cleaned = text.upper().replace("-", "_").replace(" ", "_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned or None


def normalize_tags(values: Iterable[object] | None) -> list[str]:
    """Normalise an iterable of tag-like strings.

    Drops empties and duplicates, preserves order, and quietly skips
    any value that doesn't pass :func:`normalize_tag`. **Does not**
    enforce membership in :data:`CANONICAL_STAFF_TAG_SET` — that's the
    serializer's job (raises a 400) so the model layer stays
    permissive (round-tripping legacy data is safer than crashing a
    save).
    """
    seen: set[str] = set()
    result: list[str] = []
    for raw in values or ():
        tag = normalize_tag(raw)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        result.append(tag)
    return result


def tags_for_category(category: object) -> tuple[str, ...]:
    """Return the candidate tag list for a ``StaffRequest.category``.

    Returns an empty tuple for unknown / falsy categories so callers
    can short-circuit cleanly (no special-casing of ``None``).
    """
    if not category:
        return ()
    key = str(category).upper().strip()
    return CATEGORY_TAGS.get(key, ())
