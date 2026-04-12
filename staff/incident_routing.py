"""
Default assignee resolution from Restaurant.general_settings['incident_category_assignees'].
Keys match the Settings UI (e.g. Safety, HR, Customer Issue); WhatsApp inference may use aliases like Service.
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


def _lookup_user_id(mapping: dict, incident_type: str) -> Optional[str]:
    """Return user UUID string from mapping for this category, or None."""
    raw = (incident_type or "").strip() or "General"
    candidates = [raw]
    low = raw.lower()
    if low in _ROUTING_ALIASES:
        candidates.append(_ROUTING_ALIASES[low])
    if low == "service":
        candidates.append("Customer Issue")

    uid = None
    for c in candidates:
        if c in mapping:
            uid = mapping.get(c)
            if uid:
                return str(uid)
    for c in candidates:
        c_low = c.lower()
        for k, v in mapping.items():
            if not isinstance(k, str) or not v:
                continue
            if k.lower() == c_low:
                return str(v)
    return None


def resolve_default_assignee_for_incident_type(
    restaurant: Optional["Restaurant"],
    incident_type: Optional[str],
) -> Optional["CustomUser"]:
    """
    Return the CustomUser to assign when creating an incident, or None.
    Only resolves when restaurant has a matching entry in general_settings.
    """
    if not restaurant:
        return None
    gs = restaurant.general_settings or {}
    mapping = gs.get("incident_category_assignees") or {}
    if not isinstance(mapping, dict) or not mapping:
        return None

    uid = _lookup_user_id(mapping, incident_type or "General")
    if not uid:
        return None

    from accounts.models import CustomUser  # local import avoids cycles at import time

    try:
        return CustomUser.objects.get(
            id=uid,
            restaurant_id=restaurant.id,
            is_active=True,
        )
    except (CustomUser.DoesNotExist, ValueError, TypeError) as e:
        logger.warning(
            "incident_category_assignees: invalid user id %s for restaurant %s: %s",
            uid,
            restaurant.id,
            e,
        )
        return None
