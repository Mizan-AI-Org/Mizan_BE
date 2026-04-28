"""
Calendar write endpoint — Miya can create / update meetings and reminders.

Reuses the per-tenant Google Calendar OAuth tokens that the
``MeetingsRemindersView`` already keeps fresh on
``Restaurant.general_settings['google_calendar']`` (see
``dashboard.api.meetings_reminders`` for the read path / token refresh).

Endpoints
---------
- ``POST /api/dashboard/agent/calendar-events/create/``
    Body:
        title         required
        start         required (RFC3339 / 'YYYY-MM-DDTHH:MM' / 'YYYY-MM-DD')
        end           optional (defaults to start + 1h, or all-day when start is date-only)
        description   optional
        location      optional
        attendees     optional list of emails
        all_day       optional bool (auto-detected when start is date-only)
        timezone      optional IANA tz id (defaults to restaurant.timezone or UTC)
        is_reminder   optional bool — when true, treat as a personal reminder
                      (1h block by default, no attendees, transparent='transparent')

If the tenant hasn't connected Google Calendar we return a 412
PRECONDITION_FAILED with a ``connect_url`` so Miya can hand the manager
a CTA instead of pretending it worked.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

import requests
from django.utils import timezone as dj_timezone
from rest_framework import permissions, status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from .meetings_reminders import _get_valid_access_token

logger = logging.getLogger(__name__)

_GOOGLE_EVENTS_INSERT = (
    "https://www.googleapis.com/calendar/v3/calendars/primary/events"
)
_GOOGLE_EVENTS_PATCH = _GOOGLE_EVENTS_INSERT + "/{event_id}"


def _coerce_event_time(raw, fallback_tz: str) -> tuple[dict[str, Any] | None, bool, str | None]:
    """
    Parse a flexible time string into the shape Google expects.

    Returns (event_time_object, is_all_day, error_message).

    Supported inputs:
      - "2026-05-15"               → all-day
      - "2026-05-15T14:30"         → timed in fallback_tz
      - "2026-05-15T14:30:00+01:00"→ timed with explicit offset
      - any ISO datetime           → timed
    """
    if not raw:
        return None, False, "missing time"
    raw = str(raw).strip()
    # Date-only input → all-day event.
    if len(raw) == 10 and raw.count("-") == 2:
        return ({"date": raw}, True, None)
    parsed: datetime | None = None
    try:
        if raw.endswith("Z"):
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        parsed = None
    if parsed is None:
        return None, False, f"invalid datetime: {raw!r}"
    iso = parsed.isoformat()
    body: dict[str, Any] = {"dateTime": iso}
    # Only attach the timeZone field when the input doesn't carry an
    # explicit offset — Google preserves the offset otherwise.
    if parsed.tzinfo is None:
        body["timeZone"] = fallback_tz or "UTC"
    return body, False, None


@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_create_calendar_event(request):
    """
    Create a Google Calendar event on the tenant's primary calendar.
    """
    from scheduling.views_agent import _resolve_restaurant_for_agent

    restaurant, acting_user, err = _resolve_restaurant_for_agent(request)
    if err:
        return Response({"success": False, "error": err["error"]}, status=err["status"])

    data = request.data if isinstance(getattr(request, "data", None), dict) else {}

    title = str(data.get("title") or data.get("summary") or "").strip()
    if not title:
        return Response(
            {
                "success": False,
                "error": "Missing title",
                "message_for_user": "I need a title for the event.",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    raw_start = data.get("start") or data.get("start_at") or data.get("startTime")
    raw_end = data.get("end") or data.get("end_at") or data.get("endTime")
    fallback_tz = (
        str(data.get("timezone") or "")
        or getattr(restaurant, "timezone", None)
        or "UTC"
    )

    start_obj, is_all_day, time_err = _coerce_event_time(raw_start, fallback_tz)
    if time_err:
        return Response(
            {
                "success": False,
                "error": f"Invalid start time: {time_err}",
                "message_for_user": "I couldn't read the start time. Try '2026-05-15 14:30'.",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    if raw_end:
        end_obj, _is_all_day_end, end_err = _coerce_event_time(raw_end, fallback_tz)
        if end_err:
            return Response(
                {
                    "success": False,
                    "error": f"Invalid end time: {end_err}",
                    "message_for_user": "I couldn't read the end time.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
    else:
        # Default duration: 60 min for timed, 1 day for all-day. Reminders
        # use the same defaults but render with transparent availability.
        if is_all_day:
            from datetime import date as _date

            try:
                start_date = _date.fromisoformat(start_obj["date"])  # type: ignore[index]
            except (KeyError, TypeError, ValueError):
                start_date = dj_timezone.now().date()
            end_obj = {"date": (start_date + timedelta(days=1)).isoformat()}
        else:
            try:
                start_dt = datetime.fromisoformat(start_obj["dateTime"])  # type: ignore[index]
            except (KeyError, TypeError, ValueError):
                start_dt = dj_timezone.now()
            end_dt = start_dt + timedelta(hours=1)
            end_obj = {"dateTime": end_dt.isoformat()}
            if "timeZone" in start_obj:
                end_obj["timeZone"] = start_obj["timeZone"]

    description = str(data.get("description") or data.get("notes") or "").strip()
    location = str(data.get("location") or "").strip()
    is_reminder = bool(data.get("is_reminder") or data.get("isReminder"))

    attendees = data.get("attendees") or []
    if isinstance(attendees, str):
        attendees = [a.strip() for a in attendees.split(",") if a.strip()]
    if not isinstance(attendees, list):
        attendees = []
    attendees_payload = [
        {"email": str(a).strip()} for a in attendees if isinstance(a, str) and "@" in a
    ]

    body: dict[str, Any] = {
        "summary": title[:1024],
        "start": start_obj,
        "end": end_obj,
    }
    if description:
        body["description"] = description[:8000]
    if location:
        body["location"] = location[:1024]
    if attendees_payload and not is_reminder:
        body["attendees"] = attendees_payload
    if is_reminder:
        # ``transparent`` keeps the calendar marked as available so a
        # personal reminder doesn't accidentally block other invites.
        body["transparency"] = "transparent"
        body["visibility"] = "private"

    access_token, gcal = _get_valid_access_token(restaurant)
    if not access_token:
        return Response(
            {
                "success": False,
                "error": "calendar_not_connected",
                "connected": False,
                "connect_url": "/dashboard/settings?tab=integrations",
                "message_for_user": (
                    "I can't create that yet — Google Calendar isn't connected for "
                    f"{restaurant.name}. Connect it from Settings → Integrations and "
                    "I'll be able to schedule events directly."
                ),
            },
            status=status.HTTP_412_PRECONDITION_FAILED,
        )

    # Optionally invite attendees by email — requires sendUpdates=all on
    # the request. We skip this for reminders.
    params = {}
    if attendees_payload and not is_reminder:
        params["sendUpdates"] = "all"

    try:
        r = requests.post(
            _GOOGLE_EVENTS_INSERT,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=body,
            params=params,
            timeout=10,
        )
    except requests.RequestException as exc:
        logger.exception("Google Calendar insert failed for restaurant=%s", restaurant.id)
        return Response(
            {
                "success": False,
                "error": str(exc),
                "message_for_user": "Couldn't reach Google Calendar — try again in a minute.",
            },
            status=status.HTTP_502_BAD_GATEWAY,
        )

    if r.status_code >= 400:
        logger.warning(
            "Google Calendar insert returned %s for restaurant=%s: %s",
            r.status_code, restaurant.id, r.text[:300],
        )
        return Response(
            {
                "success": False,
                "error": "google_api_error",
                "status_code": r.status_code,
                "detail": r.text[:300],
                "message_for_user": "Google Calendar rejected the event. Check the time and try again.",
            },
            status=status.HTTP_502_BAD_GATEWAY,
        )

    event = r.json() or {}
    event_id = event.get("id")
    html_link = event.get("htmlLink")

    # Friendly summary for the chat reply.
    when_display = ""
    start_iso = (start_obj or {}).get("dateTime") or (start_obj or {}).get("date") or ""
    if start_iso:
        when_display = (
            start_iso.replace("T", " ").split("+")[0][:16]
            if "T" in start_iso
            else start_iso
        )
    label = "reminder" if is_reminder else "meeting"
    msg = f"📅 Created {label} \"{title}\""
    if when_display:
        msg += f" on {when_display}"
    if attendees_payload and not is_reminder:
        msg += f" with {len(attendees_payload)} attendee{'s' if len(attendees_payload) != 1 else ''}"
    msg += "."
    if html_link:
        msg += f" {html_link}"

    return Response(
        {
            "success": True,
            "event_id": event_id,
            "html_link": html_link,
            "calendar_event": {
                "id": event_id,
                "summary": event.get("summary"),
                "start": event.get("start"),
                "end": event.get("end"),
                "html_link": html_link,
                "status": event.get("status"),
                "transparency": event.get("transparency"),
            },
            "message_for_user": msg,
        },
        status=status.HTTP_201_CREATED,
    )
