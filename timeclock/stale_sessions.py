"""Close forgotten open clock-ins so a new shift can start cleanly."""
from __future__ import annotations

import logging
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)


def _shift_recipients_filter(user):
    return Q(staff=user) | Q(staff_members=user)


def close_stale_open_clock_in(user, *, now=None, source: str = "auto") -> tuple[bool, object | None]:
    """
    If the staff member still has an open clock-in that should be closed,
    write a matching clock-out event.

    Returns (closed, clock_out_event).
    """
    from scheduling.models import AssignedShift
    from timeclock.models import ClockEvent

    if not user:
        return False, None

    now = now or timezone.now()
    last_event = ClockEvent.objects.filter(staff=user).order_by("-timestamp").first()
    if not last_event or last_event.event_type != "in":
        return False, None

    last_ts = last_event.timestamp
    last_local_date = timezone.localtime(last_ts).date()
    now_local_date = timezone.localtime(now).date()
    restaurant = getattr(user, "restaurant", None)
    auto_enabled = bool(getattr(restaurant, "automatic_clock_out", True))

    stale = False
    auto_out_at = now
    notes = ""

    if last_local_date < now_local_date:
        stale = True
        eight_hours_later = last_ts + timedelta(hours=8)
        end_of_that_day = last_ts.replace(hour=23, minute=59, second=59, microsecond=0)
        auto_out_at = min(eight_hours_later, end_of_that_day)
        notes = (
            "Auto clock-out: previous clock-in was left open across days. "
            "Closed so today's session can start fresh."
        )
    else:
        today_shifts = AssignedShift.objects.filter(
            _shift_recipients_filter(user),
            shift_date=now_local_date,
        )
        active_shift_exists = today_shifts.filter(
            status__in=["SCHEDULED", "CONFIRMED", "IN_PROGRESS"],
            end_time__gt=now,
        ).exists()
        last_shift = today_shifts.order_by("-end_time").first()
        session_age = now - last_ts

        if last_shift and last_shift.end_time and last_shift.end_time <= now and not active_shift_exists:
            stale = True
            auto_out_at = last_shift.end_time if last_shift.end_time > last_ts else now
            notes = "Auto clock-out: scheduled shift ended while clock-in was still open."
        elif auto_enabled and session_age > timedelta(hours=12):
            stale = True
            auto_out_at = last_ts + timedelta(hours=12)
            notes = "Auto clock-out: open session exceeded 12 hours."
        elif auto_enabled and not today_shifts.exists() and session_age > timedelta(hours=8):
            stale = True
            auto_out_at = last_ts + timedelta(hours=8)
            notes = "Auto clock-out: no scheduled shift and session left open."

    if not stale:
        return False, None

    try:
        auto_out = ClockEvent.objects.create(
            staff=user,
            event_type="out",
            latitude=None,
            longitude=None,
            device_id=source[:255],
            notes=notes,
            location=getattr(last_event, "location", None),
            location_mismatch=False,
        )
        ClockEvent.objects.filter(pk=auto_out.pk).update(timestamp=auto_out_at)
        logger.info(
            "close_stale_open_clock_in user=%s in=%s out=%s reason=%s",
            getattr(user, "id", None),
            last_event.id,
            auto_out.id,
            notes[:80],
        )
        return True, auto_out
    except Exception:
        logger.exception(
            "close_stale_open_clock_in failed user=%s last_in=%s",
            getattr(user, "id", None),
            getattr(last_event, "id", None),
        )
        return False, None


def is_open_clock_in_active(user, *, now=None) -> bool:
    """True only when the latest clock event is an open 'in' that is not stale."""
    from timeclock.models import ClockEvent

    if not user:
        return False
    now = now or timezone.now()
    last_event = ClockEvent.objects.filter(staff=user).order_by("-timestamp").first()
    if not last_event or last_event.event_type != "in":
        return False
    closed, _ = close_stale_open_clock_in(user, now=now, source="stale_check")
    if closed:
        return False
    return True
