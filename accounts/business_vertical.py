"""
Allowed values for Restaurant.general_settings['business_vertical'].
Used by registration, unified settings PATCH, and staff invite validation (frontend mirrors groups).
"""
from __future__ import annotations

from typing import Optional, Tuple

ALLOWED_BUSINESS_VERTICALS = frozenset(
    {
        "RESTAURANT",
        "RETAIL",
        "MANUFACTURING",
        "CONSTRUCTION",
        "HEALTHCARE",
        "HOSPITALITY",
        "SERVICES",
        "OTHER",
    }
)


def validate_business_vertical(raw) -> Tuple[bool, Optional[str]]:
    """
    Strict validation for writes. Returns (ok, error_message).
    """
    if raw is None or str(raw).strip() == "":
        return True, None
    bv = str(raw).strip().upper()
    if bv not in ALLOWED_BUSINESS_VERTICALS:
        allowed = ", ".join(sorted(ALLOWED_BUSINESS_VERTICALS))
        return False, f"business_vertical must be one of: {allowed}"
    return True, None
