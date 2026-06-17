"""Compute staff hours from clock events for payroll."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from timeclock.models import ClockEvent


def staff_hours_from_clock_events(staff, start_date: date, end_date: date) -> Decimal:
    """Pair clock in/out events and return total hours worked."""
    events = (
        ClockEvent.objects.filter(
            staff=staff,
            timestamp__date__gte=start_date,
            timestamp__date__lte=end_date,
        )
        .order_by("timestamp")
        .values("event_type", "timestamp")
    )
    total_seconds = 0.0
    current_in = None
    for e in events:
        if e["event_type"] == "in":
            current_in = e["timestamp"]
        elif e["event_type"] == "out" and current_in is not None:
            total_seconds += (e["timestamp"] - current_in).total_seconds()
            current_in = None
    return Decimal(str(round(total_seconds / 3600, 2)))


def staff_hours_map_for_restaurant(restaurant, start_date: date, end_date: date) -> dict[str, Decimal]:
    """Return {staff_id: hours} for all staff with clock activity."""
    events = (
        ClockEvent.objects.filter(
            staff__restaurant=restaurant,
            timestamp__date__gte=start_date,
            timestamp__date__lte=end_date,
        )
        .order_by("staff_id", "timestamp")
        .values("staff_id", "event_type", "timestamp")
    )
    current_in: dict[str, object] = {}
    totals: dict[str, float] = defaultdict(float)
    for e in events:
        sid = str(e["staff_id"])
        if e["event_type"] == "in":
            current_in[sid] = e["timestamp"]
        elif e["event_type"] == "out" and sid in current_in:
            delta = e["timestamp"] - current_in[sid]
            totals[sid] += delta.total_seconds() / 3600
            del current_in[sid]
    return {k: Decimal(str(round(v, 2))) for k, v in totals.items()}
