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


def agent_time(value: Any) -> str | None:
    """Parse agent time values to HH:MM (accepts HH:MM:SS and ISO datetimes)."""
    text = agent_scalar(value)
    if not text:
        return None
    text = text.strip()
    if "T" in text:
        time_part = text.split("T", 1)[1]
        for sep in ("+", "Z"):
            if sep in time_part:
                time_part = time_part.split(sep, 1)[0]
        if len(time_part) >= 5 and time_part[2] == ":":
            return time_part[:5]
        return None
    if len(text) >= 8 and text[2] == ":" and text[5] == ":":
        return text[:5]
    if len(text) >= 5 and text[2] == ":":
        return text[:5]
    return None


def _infer_service_times(*hints: str) -> tuple[str, str] | None:
    combined = " ".join((h or "").lower() for h in hints)
    if any(k in combined for k in ("dinner", "evening", "soir")):
        return "18:00", "23:00"
    if any(k in combined for k in ("lunch", "midday", "midi")):
        return "11:00", "15:00"
    if any(k in combined for k in ("breakfast", "morning", "matin")):
        return "07:00", "11:00"
    return None


def enrich_create_shift_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize Miya/Mastra create_shift args: default date to today, resolve staff_name
    aliases, parse ISO datetimes, infer dinner/lunch/breakfast hours.
    """
    from django.utils import timezone

    out = dict(payload or {})

    for src in ("name", "staffName", "employee_name", "employee", "staff"):
        if not out.get("staff_name") and out.get(src):
            val = out[src]
            if isinstance(val, str):
                out["staff_name"] = val.strip()
            elif isinstance(val, dict):
                out["staff_name"] = (
                    f"{val.get('first_name') or ''} {val.get('last_name') or ''}".strip()
                    or str(val.get("name") or "")
                ).strip() or None

    if not out.get("shift_date"):
        for key in ("date", "shiftDate", "day"):
            d = agent_date(out.get(key))
            if d:
                out["shift_date"] = d.isoformat()
                break

    if not out.get("shift_date"):
        for key in ("start_time", "startTime", "start", "starts_at", "startsAt"):
            d = agent_date(out.get(key))
            if d:
                out["shift_date"] = d.isoformat()
                break

    if not out.get("shift_date"):
        out["shift_date"] = timezone.localdate().isoformat()

    for key, alt in (("start_time", "startTime"), ("end_time", "endTime")):
        parsed = agent_time(out.get(key) or out.get(alt))
        if parsed:
            out[key] = parsed

    service_hint = " ".join(
        str(out.get(k) or "")
        for k in ("notes", "role", "service", "shift_type", "description", "title")
    )
    inferred = _infer_service_times(service_hint)
    if inferred:
        if not out.get("start_time"):
            out["start_time"] = inferred[0]
        if not out.get("end_time"):
            out["end_time"] = inferred[1]

    if not out.get("start_time"):
        out["start_time"] = "09:00"
    if not out.get("end_time"):
        out["end_time"] = "17:00"

    return out
