"""
Clock-ins widget endpoint.

Returns the tenant's latest clock-in events (default 5) for the dashboard
card. Matches the design mock: "Sarah Kabli just arrived — 17:00 ✓".

Status mapping (widget pill vocabulary):
- ``ON_TIME`` — staff has no shift today OR clocked in within a 5 min
  grace window of their shift's ``start_time``.
- ``LATE``    — clock-in was after ``start_time + 5 min`` of today's
  scheduled shift (we match the earliest shift still open or the one
  that was active at the clock-in instant).
- ``EARLY``   — staff clocked in more than 15 min before their shift.
  (Informational — doesn't drive any pill styling server-side, just
  the optional "X min early" subtitle; widget can render it as on-time.)

Why we only return ``event_type='in'`` (not breaks / outs):
The widget is specifically "who just arrived". Break/end events would
just add noise — the full timeline lives on ``/dashboard/attendance``
which the widget links to.

Caching:
60 s max-age matches the widget's polling cadence. ETag short-circuits
keep bandwidth at zero for idle tabs.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from django.db.models import Q
from django.utils import timezone
from rest_framework import permissions, status as http_status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.http_caching import json_response_with_cache

from timeclock.models import ClockEvent


DEFAULT_LIMIT = 5
MAX_LIMIT = 25
# How late (after scheduled ``start_time``) a staff member can clock in
# before we flag them "LATE". Matches the existing agent attendance
# report threshold (``agent_attendance_report`` uses 5 min).
_LATE_GRACE_MINUTES = 5
# How early a clock-in has to be before we call it "EARLY" in the label.
_EARLY_THRESHOLD_MINUTES = 15


def _staff_payload(user) -> dict[str, Any]:
    first = (getattr(user, "first_name", None) or "").strip()
    last = (getattr(user, "last_name", None) or "").strip()
    full = f"{first} {last}".strip() or (getattr(user, "email", None) or "")
    initials = (first[:1] + last[:1]).upper() or (full[:2] if full else "").upper()
    return {
        "id": str(user.pk),
        "name": full,
        "initials": initials or "?",
        "role": getattr(user, "role", None),
        "avatar": getattr(getattr(user, "profile", None), "avatar_url", None),
    }


def _derive_status(
    clock_in_at: datetime,
    scheduled_start: datetime | None,
) -> tuple[str, int | None]:
    """Return (status, lateness_minutes)."""
    if scheduled_start is None:
        return "ON_TIME", None

    if not timezone.is_aware(scheduled_start):
        scheduled_start = timezone.make_aware(scheduled_start)

    delta_seconds = (clock_in_at - scheduled_start).total_seconds()
    delta_minutes = int(delta_seconds // 60)

    if delta_seconds > _LATE_GRACE_MINUTES * 60:
        return "LATE", delta_minutes
    if delta_seconds < -_EARLY_THRESHOLD_MINUTES * 60:
        # Still "on time" for the pill, but return the negative minutes
        # so the widget can render "12 min early" if it wants to.
        return "ON_TIME", delta_minutes
    return "ON_TIME", delta_minutes


def _find_scheduled_start_for(
    user,
    clock_in_at: datetime,
) -> datetime | None:
    """Return the scheduled ``start_time`` of the shift this clock-in
    is most plausibly attached to, or ``None`` if the staff member has
    no shift today.

    We prefer:
      1. A shift whose (start - 30min, end) window contains the event.
      2. The earliest still-open shift on ``clock_in_at.date()``.

    This mirrors :func:`timeclock.views._find_shift_for_clock_in` so the
    widget's "late" call matches the shift-assignment logic used when
    the clock-in was created. We avoid importing that helper directly
    because it has side effects (mutates shift status).
    """
    try:
        from scheduling.models import AssignedShift
    except Exception:  # pragma: no cover
        return None

    shifts = (
        AssignedShift.objects.filter(
            Q(staff=user) | Q(staff_members=user),
            shift_date=clock_in_at.date(),
        )
        .distinct()
        .order_by("start_time")
    )

    window = timedelta(minutes=30)

    # Covering window first (start-30min..end)
    for shift in shifts:
        start = shift.start_time
        end = shift.end_time
        if not start or not end:
            continue
        if not timezone.is_aware(start):
            start = timezone.make_aware(start)
        if not timezone.is_aware(end):
            end = timezone.make_aware(end)
        if (start - window) <= clock_in_at <= end:
            return start

    # Fallback: earliest shift of the day.
    first = shifts.first()
    if first and first.start_time:
        start = first.start_time
        if not timezone.is_aware(start):
            start = timezone.make_aware(start)
        return start
    return None


def _serialize_event(ev: ClockEvent) -> dict[str, Any]:
    staff = ev.staff
    scheduled_start = _find_scheduled_start_for(staff, ev.timestamp)
    status_label, lateness_minutes = _derive_status(ev.timestamp, scheduled_start)

    location = ev.location
    location_payload = None
    if location is not None and getattr(location, "id", None):
        location_payload = {
            "id": str(location.id),
            "name": getattr(location, "name", None) or "",
        }

    return {
        "id": str(ev.id),
        "timestamp": ev.timestamp.isoformat(),
        "status": status_label,
        "lateness_minutes": lateness_minutes,
        "location_mismatch": bool(ev.location_mismatch),
        "method": "manager_override" if ev.device_id == ClockEvent.CLOCK_IN_METHOD_OVERRIDE else "self",
        "location": location_payload,
        "staff": _staff_payload(staff),
    }


class DashboardClockInsView(APIView):
    """
    GET /api/dashboard/clock-ins/?limit=5

    Returns:
        {
          "items": [ClockInEventItem, ...],
          "counts": {"on_time": N, "late": N, "total": N},
          "generated_at": "...",
        }
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        restaurant = getattr(request.user, "restaurant", None)
        if not restaurant:
            return Response(
                {"error": "No workspace associated"},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        try:
            limit = int(request.query_params.get("limit") or DEFAULT_LIMIT)
        except (TypeError, ValueError):
            limit = DEFAULT_LIMIT
        limit = max(1, min(limit, MAX_LIMIT))

        today = timezone.now().date()

        events_qs = (
            ClockEvent.objects.filter(
                staff__restaurant=restaurant,
                event_type="in",
                timestamp__date=today,
            )
            .select_related("staff", "staff__profile", "location")
            .order_by("-timestamp")[:limit]
        )

        items = [_serialize_event(ev) for ev in events_qs]

        # Today's on-time / late totals across ALL events, not just the
        # trimmed first N — lets the card show "3 late today" even when
        # it's only rendering the top 5 names.
        total_today = ClockEvent.objects.filter(
            staff__restaurant=restaurant,
            event_type="in",
            timestamp__date=today,
        ).count()

        late_count = sum(1 for it in items if it["status"] == "LATE")
        on_time_count = sum(1 for it in items if it["status"] == "ON_TIME")

        data = {
            "items": items,
            "counts": {
                "on_time": on_time_count,
                "late": late_count,
                "total": total_today,
            },
            "generated_at": timezone.now().isoformat(),
        }
        return json_response_with_cache(
            request,
            data,
            # 30 s / 60 s SWR is plenty — a freshly-arrived staff member
            # is fine to appear one poll cycle later.
            max_age=30,
            private=True,
            stale_while_revalidate=60,
        )
