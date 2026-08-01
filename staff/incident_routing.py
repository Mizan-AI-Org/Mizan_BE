"""
Default assignee resolution from Restaurant.general_settings['incident_category_assignees'].
Keys match the Settings UI (e.g. Safety, HR, Customer Issue); WhatsApp inference may use aliases like Service.

Also fans out informed notifications using ``category_owners`` incident.* slug
arrays when multiple owners are configured (primary remains a single FK).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from accounts.models import CustomUser, Restaurant

logger = logging.getLogger(__name__)

# Canonical keys match General Settings → Incident routing (and SafetyConcernReport.incident_type)
CANONICAL_INCIDENT_CATEGORIES = (
    "Safety",
    "Maintenance",
    "HR",
    "Food Safety",
    "Customer Issue",
    "General",
)

# Map canonical incident labels → onboarding category_owners slugs so we can
# fan out WhatsApp to every configured owner (not just the legacy single UID).
_INCIDENT_LABEL_TO_OWNER_SLUGS: dict[str, tuple[str, ...]] = {
    "Safety": ("incident.safety",),
    "Maintenance": ("incident.equipment", "request.maintenance"),
    "HR": ("incident.hr", "request.hr"),
    "Food Safety": ("incident.quality",),
    "Customer Issue": ("incident.customer",),
    "General": ("incident.other",),
    "Equipment Failure": ("incident.equipment",),
    "Security": ("incident.security",),
    "Food Quality": ("incident.quality",),
    "Other": ("incident.other",),
    "Service": ("incident.customer",),
}


def normalize_incident_category_for_storage(category: Optional[str]) -> str:
    """
    Map agent/Miya or API input to a canonical incident_type string used in DB and settings.
    Legacy: ``Service`` → ``Customer Issue`` (same as WhatsApp infer_incident_type).
    """
    if not category or not str(category).strip():
        return "General"
    raw = str(category).strip()
    key = raw.lower().replace("_", " ")
    aliases = {
        "service": "Customer Issue",
        "other": "General",
        "food safety": "Food Safety",
        "customer issue": "Customer Issue",
        "hr": "HR",
        "maintenance": "Maintenance",
        "safety": "Safety",
        "general": "General",
    }
    if key in aliases:
        return aliases[key]
    if raw in CANONICAL_INCIDENT_CATEGORIES:
        return raw
    if raw == "Service":
        return "Customer Issue"
    return "General"


# Inferred / legacy incident_type strings -> key stored in settings JSON
_ROUTING_ALIASES = {
    "service": "Customer Issue",
    "other": "General",
}


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


def _lookup_user_ids(mapping: dict, incident_type: str) -> list[str]:
    """Return ordered UUID strings from mapping for this category."""
    raw = (incident_type or "").strip() or "General"
    candidates = [raw]
    low = raw.lower()
    if low in _ROUTING_ALIASES:
        candidates.append(_ROUTING_ALIASES[low])
    if low == "service":
        candidates.append("Customer Issue")

    seen: set[str] = set()
    out: list[str] = []

    def _consume(val) -> None:
        for uid in _uids_from_raw(val):
            if uid not in seen:
                seen.add(uid)
                out.append(uid)

    for c in candidates:
        if c in mapping and mapping.get(c):
            _consume(mapping.get(c))
            if out:
                return out
    for c in candidates:
        c_low = c.lower()
        for k, v in mapping.items():
            if not isinstance(k, str) or not v:
                continue
            if k.lower() == c_low:
                _consume(v)
                if out:
                    return out
    return out


def _lookup_user_id(mapping: dict, incident_type: str) -> Optional[str]:
    """Return first user UUID string from mapping for this category, or None."""
    uids = _lookup_user_ids(mapping, incident_type)
    return uids[0] if uids else None


def resolve_default_assignee_for_incident_type(
    restaurant: Optional["Restaurant"],
    incident_type: Optional[str],
) -> Optional["CustomUser"]:
    """
    Return the primary CustomUser to assign when creating an incident, or None.
    Equivalent to the first entry of
    :func:`resolve_all_assignees_for_incident_type`.
    """
    owners = resolve_all_assignees_for_incident_type(restaurant, incident_type)
    return owners[0] if owners else None


def resolve_all_assignees_for_incident_type(
    restaurant: Optional["Restaurant"],
    incident_type: Optional[str],
) -> list["CustomUser"]:
    """
    Return all active owners for an incident category.

    Prefers ``category_owners`` incident.* slug arrays (multi-person), then
    falls back to legacy ``incident_category_assignees`` (single or list).
    """
    if not restaurant:
        return []

    from accounts.models import CustomUser  # local import avoids cycles at import time

    canonical = normalize_incident_category_for_storage(incident_type)
    gs = restaurant.general_settings or {}
    seen: set[str] = set()
    users: list["CustomUser"] = []

    def _append_uid(uid: str) -> None:
        if not uid or uid in seen:
            return
        try:
            user = CustomUser.objects.get(
                id=uid,
                restaurant_id=restaurant.id,
                is_active=True,
            )
        except (CustomUser.DoesNotExist, ValueError, TypeError) as e:
            logger.warning(
                "incident assignees: invalid user id %s for restaurant %s: %s",
                uid,
                restaurant.id,
                e,
            )
            return
        seen.add(uid)
        users.append(user)

    # 1) category_owners multi-person fan-out
    owners_map = gs.get("category_owners") or {}
    if isinstance(owners_map, dict) and owners_map:
        slugs = list(_INCIDENT_LABEL_TO_OWNER_SLUGS.get(canonical, ()))
        raw_label = (incident_type or "").strip()
        if raw_label and raw_label != canonical:
            slugs.extend(_INCIDENT_LABEL_TO_OWNER_SLUGS.get(raw_label, ()))
        # De-dupe slug order while preserving priority
        slug_seen: set[str] = set()
        ordered_slugs: list[str] = []
        for slug in slugs:
            key = slug.lower()
            if key in slug_seen:
                continue
            slug_seen.add(key)
            ordered_slugs.append(slug)
        lowered = {
            str(k).lower(): v for k, v in owners_map.items() if isinstance(k, str)
        }
        for slug in ordered_slugs:
            raw_val = owners_map.get(slug)
            if raw_val is None:
                raw_val = lowered.get(slug.lower())
            for uid in _uids_from_raw(raw_val):
                _append_uid(uid)
        if users:
            return users

    # 2) Legacy incident_category_assignees
    mapping = gs.get("incident_category_assignees") or {}
    if isinstance(mapping, dict) and mapping:
        for uid in _lookup_user_ids(mapping, canonical or incident_type or "General"):
            _append_uid(uid)

    return users
