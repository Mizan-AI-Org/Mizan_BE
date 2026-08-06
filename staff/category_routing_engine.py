"""
Category routing engine — multi-owner assignment and notification strategies.

Configuration lives in ``Restaurant.general_settings``:

``category_owners`` — slug → ``string[]`` of user UUIDs (primary list).

``category_routing`` — optional per-slug policy::

    {
      "request.finance": {
        "strategy": "notify_all" | "first_available" | "round_robin",
        "backup": ["<uuid>", ...]
      }
    }

``category_routing_state`` — persisted round-robin cursor per slug (internal).

Strategies:
  - ``notify_all``: primary = first (or round-robin pick); every owner notified.
  - ``first_available``: primary = first (or round-robin); only primary notified.
  - ``round_robin``: rotate primary across owners; notification follows strategy
    (defaults to notify_all when multiple owners exist).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Optional

if TYPE_CHECKING:
    from accounts.models import CustomUser, Restaurant

logger = logging.getLogger(__name__)

STRATEGY_NOTIFY_ALL = "notify_all"
STRATEGY_FIRST_AVAILABLE = "first_available"
STRATEGY_ROUND_ROBIN = "round_robin"

VALID_STRATEGIES = frozenset(
    {STRATEGY_NOTIFY_ALL, STRATEGY_FIRST_AVAILABLE, STRATEGY_ROUND_ROBIN}
)


@dataclass
class CategoryRoutingResult:
    primary: Optional["CustomUser"] = None
    owners: list["CustomUser"] = field(default_factory=list)
    informed: list["CustomUser"] = field(default_factory=list)
    notify_targets: list["CustomUser"] = field(default_factory=list)
    strategy: str = STRATEGY_FIRST_AVAILABLE
    slug: str | None = None


def _uids_from_raw(raw) -> list[str]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, (list, tuple)):
        out: list[str] = []
        for item in raw:
            out.extend(_uids_from_raw(item))
        return out
    uid = str(raw).strip()
    return [uid] if uid else []


def _lookup_user(restaurant: "Restaurant", uid: str):
    from accounts.models import CustomUser

    try:
        return CustomUser.objects.get(
            id=uid,
            restaurant_id=restaurant.id,
            is_active=True,
        )
    except (CustomUser.DoesNotExist, ValueError, TypeError):
        return None


def _all_uids_for_slugs(mapping: dict, slugs: Iterable[str]) -> tuple[list[str], str | None]:
    seen: set[str] = set()
    out: list[str] = []
    matched_slug: str | None = None

    def _consume(raw, slug: str) -> None:
        nonlocal matched_slug
        for uid in _uids_from_raw(raw):
            if uid not in seen:
                seen.add(uid)
                out.append(uid)
                if matched_slug is None:
                    matched_slug = slug

    for slug in slugs:
        if slug in mapping:
            _consume(mapping.get(slug), slug)
    lowered = {str(k).lower(): (k, v) for k, v in mapping.items() if isinstance(k, str)}
    for slug in slugs:
        key = slug.lower()
        if key in lowered and slug not in mapping:
            orig_slug, val = lowered[key]
            _consume(val, str(orig_slug))
    return out, matched_slug


def _routing_policy(gs: dict, slug: str | None) -> dict:
    policies = gs.get("category_routing") or {}
    if not isinstance(policies, dict) or not slug:
        return {}
    if slug in policies and isinstance(policies.get(slug), dict):
        return dict(policies[slug])
    lowered = {str(k).lower(): v for k, v in policies.items() if isinstance(k, str)}
    raw = lowered.get(str(slug).lower())
    return dict(raw) if isinstance(raw, dict) else {}


def _default_strategy(owner_count: int) -> str:
    if owner_count > 1:
        return STRATEGY_NOTIFY_ALL
    return STRATEGY_FIRST_AVAILABLE


def _advance_round_robin(restaurant: "Restaurant", slug: str, owner_count: int) -> int:
    if owner_count <= 0:
        return 0
    gs = dict(restaurant.general_settings or {})
    state = dict(gs.get("category_routing_state") or {})
    idx = int(state.get(slug, 0)) % owner_count
    state[slug] = (idx + 1) % owner_count
    gs["category_routing_state"] = state
    restaurant.general_settings = gs
    restaurant.save(update_fields=["general_settings"])
    return idx


def resolve_routing_for_slugs(
    restaurant: Optional["Restaurant"],
    slugs: tuple[str, ...],
) -> CategoryRoutingResult:
    """Resolve primary assignee and notification targets for slug list."""
    result = CategoryRoutingResult()
    if not restaurant or not slugs:
        return result

    gs = restaurant.general_settings or {}
    mapping = gs.get("category_owners") or {}
    if not isinstance(mapping, dict):
        return result

    uids, matched_slug = _all_uids_for_slugs(mapping, slugs)
    policy = _routing_policy(gs, matched_slug)
    backup_uids = _uids_from_raw(policy.get("backup"))
    for uid in backup_uids:
        if uid not in uids:
            uids.append(uid)

    owners: list = []
    seen_ids: set[str] = set()
    for uid in uids:
        user = _lookup_user(restaurant, uid)
        if user is None:
            continue
        sid = str(user.id)
        if sid in seen_ids:
            continue
        seen_ids.add(sid)
        owners.append(user)

    if not owners:
        return result

    strategy = str(policy.get("strategy") or "").strip().lower()
    if strategy not in VALID_STRATEGIES:
        strategy = _default_strategy(len(owners))

    result.owners = owners
    result.slug = matched_slug
    result.strategy = strategy

    primary_idx = 0
    if strategy == STRATEGY_ROUND_ROBIN and matched_slug:
        primary_idx = _advance_round_robin(restaurant, matched_slug, len(owners))
    elif strategy == STRATEGY_ROUND_ROBIN:
        primary_idx = 0

    result.primary = owners[primary_idx]
    primary_id = str(result.primary.id)

    if strategy == STRATEGY_FIRST_AVAILABLE:
        result.notify_targets = [result.primary]
        result.informed = []
    else:
        result.notify_targets = list(owners)
        result.informed = [u for u in owners if str(u.id) != primary_id]

    return result


def resolve_routing_for_staff_category(
    restaurant: Optional["Restaurant"],
    category: Optional[str],
) -> CategoryRoutingResult:
    from staff.request_routing import slugs_for_category

    return resolve_routing_for_slugs(restaurant, slugs_for_category(category))


def resolve_routing_for_incident_type(
    restaurant: Optional["Restaurant"],
    incident_type: Optional[str],
) -> CategoryRoutingResult:
    from staff.incident_routing import (
        _INCIDENT_LABEL_TO_OWNER_SLUGS,
        normalize_incident_category_for_storage,
    )

    canonical = normalize_incident_category_for_storage(incident_type)
    slugs = list(_INCIDENT_LABEL_TO_OWNER_SLUGS.get(canonical, ()))
    raw_label = (incident_type or "").strip()
    if raw_label and raw_label != canonical:
        slugs.extend(_INCIDENT_LABEL_TO_OWNER_SLUGS.get(raw_label, ()))
    slug_seen: set[str] = set()
    ordered: list[str] = []
    for slug in slugs:
        key = slug.lower()
        if key in slug_seen:
            continue
        slug_seen.add(key)
        ordered.append(slug)
    return resolve_routing_for_slugs(restaurant, tuple(ordered))
