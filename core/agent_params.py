"""Normalize agent API query/body params (QueryDict lists, nested values, dates)."""

from __future__ import annotations

from datetime import date
from typing import Any

from django.utils.dateparse import parse_date


def agent_scalar(value: Any) -> str | None:
    """Return a single string from QueryDict/list/scalar values."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        value = value[0]
    if isinstance(value, dict):
        return None
    text = str(value).strip()
    return text if text else None


def agent_date(value: Any) -> date | None:
    """Parse YYYY-MM-DD (or ISO datetime prefix) from agent params."""
    text = agent_scalar(value)
    if not text:
        return None
    # Accept full ISO timestamps — use date portion only.
    return parse_date(text[:10])


def agent_params_from_request(request) -> dict[str, str | None]:
    """Flatten query params + optional JSON body to scalar strings."""
    params: dict[str, str | None] = {}
    for key, val in dict(getattr(request, "query_params", {}) or {}).items():
        params[str(key)] = agent_scalar(val)

    if getattr(request, "method", "").upper() == "POST":
        data = getattr(request, "data", None)
        if isinstance(data, dict):
            for key, val in data.items():
                scalar = agent_scalar(val)
                if scalar is not None:
                    params[str(key)] = scalar

    return params


def resolve_shift_date_range(
    params: dict[str, str | None],
    *,
    default_today: bool = False,
) -> tuple[date | None, date | None]:
    """Resolve date_from/date_to from common agent param aliases."""
    single = agent_date(params.get("date"))
    date_from = agent_date(params.get("date_from") or params.get("start_date"))
    date_to = agent_date(params.get("date_to") or params.get("end_date"))

    if single and not date_from:
        date_from = single
    if single and not date_to:
        date_to = single

    if date_from and not date_to:
        date_to = date_from
    if date_to and not date_from:
        date_from = date_to

    if default_today and not date_from and not date_to:
        from django.utils import timezone

        today = timezone.localdate()
        return today, today

    return date_from, date_to
