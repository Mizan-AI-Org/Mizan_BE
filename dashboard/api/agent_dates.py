"""Forgiving date parsing for Miya agent payloads (chat, vision, OCR)."""
from __future__ import annotations

from datetime import date, timedelta

from dateutil import parser as dateutil_parser
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime


def coerce_agent_date(raw) -> date | None:
    """Parse YYYY-MM-DD, friendly aliases, and human-readable dates."""
    if raw is None:
        return None
    if isinstance(raw, date):
        return raw

    text = str(raw).strip()
    if not text:
        return None

    iso = parse_date(text)
    if iso:
        return iso

    dt = parse_datetime(text)
    if dt:
        return dt.date()

    lowered = text.lower()
    today = timezone.now().date()
    if lowered in ("today", "now"):
        return today
    if lowered == "tomorrow":
        return today + timedelta(days=1)
    if lowered == "yesterday":
        return today - timedelta(days=1)

    try:
        parsed = dateutil_parser.parse(text, dayfirst=False, fuzzy=True)
        return parsed.date()
    except (ValueError, TypeError, OverflowError):
        return None
