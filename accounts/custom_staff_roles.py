"""
Custom staff role titles for any business vertical (optional layer on top of preset roles).

Stored in Restaurant.general_settings['custom_staff_roles'] as:
  [{"id": "<uuid>", "name": "Display name"}, ...]

Invites use role=CUSTOM plus custom_role_id referencing one entry.
"""
from __future__ import annotations

import uuid
from typing import Any, List, Optional, Tuple


MAX_CUSTOM_ROLES = 40
MAX_NAME_LEN = 80


def normalize_custom_staff_roles_payload(raw: Any) -> List[dict]:
    """
    Validate and normalize list for storage. Raises ValueError on invalid input.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("custom_staff_roles must be a list")
    out: List[dict] = []
    seen: set = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if not name:
            raise ValueError(f"Custom role name is required (index {i})")
        if len(name) > MAX_NAME_LEN:
            raise ValueError(f"Custom role name too long (max {MAX_NAME_LEN} characters)")
        rid = (item.get("id") or "").strip()
        if not rid:
            rid = str(uuid.uuid4())
        if rid in seen:
            continue
        seen.add(rid)
        out.append({"id": rid, "name": name})
        if len(out) > MAX_CUSTOM_ROLES:
            raise ValueError(f"Too many custom roles (max {MAX_CUSTOM_ROLES})")
    return out


def get_restaurant_custom_roles(restaurant) -> List[dict]:
    gs = getattr(restaurant, "general_settings", None) or {}
    raw = gs.get("custom_staff_roles")
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, dict) and x.get("id") and x.get("name")]


def resolve_custom_role_name(restaurant, role_id: str) -> str:
    rid = (role_id or "").strip()
    if not rid:
        return ""
    for r in get_restaurant_custom_roles(restaurant):
        if str(r.get("id")) == rid:
            return (r.get("name") or "").strip()
    return ""


def validate_custom_invite(
    restaurant, role: str, custom_role_id: Optional[str]
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    For role=CUSTOM, ensure custom_role_id matches a defined title in general_settings.
    Returns (ok, error_message, resolved_label).
    """
    role = (role or "").strip().upper()
    if role != "CUSTOM":
        return True, None, None
    cid = (custom_role_id or "").strip()
    if not cid:
        return False, "custom_role_id is required when role is CUSTOM.", None
    label = resolve_custom_role_name(restaurant, cid)
    if not label:
        return False, "Invalid custom_role_id.", None
    return True, None, label
