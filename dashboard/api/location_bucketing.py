"""Shared helpers for attributing shifts/clocks/requests to a branch."""
from __future__ import annotations

from typing import Any


def resolve_location_bucket(
    location_id: Any | None,
    *,
    staff_primary_location_id: Any | None = None,
    known_location_ids: set[Any],
    primary_location_id: Any | None,
) -> Any | None:
    """
    Pick which branch bucket a row belongs to.

    Priority: explicit location FK → staff home branch → tenant primary.
    """
    if location_id in known_location_ids:
        return location_id
    if staff_primary_location_id in known_location_ids:
        return staff_primary_location_id
    return primary_location_id
