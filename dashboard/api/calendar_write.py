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
        title         required (unless ``events`` batch is provided)
        start         required (RFC3339 / 'YYYY-MM-DDTHH:MM' / 'YYYY-MM-DD')
        end           optional (defaults to start + 1h, or all-day when start is date-only)
        description   optional
        location      optional
        attendees     optional list of emails
        all_day       optional bool (auto-detected when start is date-only)
        timezone      optional IANA tz id (defaults to restaurant.timezone or UTC)
        is_reminder   optional bool — when true, treat as a personal reminder
                      (1h block by default, no attendees, transparent='transparent')
        events / meetings / appointments  optional list of {title, start, end, …}
                      for batch create (up to 20)

- ``GET /api/dashboard/agent/calendar-events/list/?q=Loubna``
    Search upcoming/recent meetings by keyword (title, location).

- ``POST /api/dashboard/agent/calendar-events/update/``
    Body: event_id (or q to resolve one match), plus fields to patch
    (location, start, end, title, description).

- ``POST /api/dashboard/agent/calendar-events/delete/``
    Body: event_id (or q to resolve one match) — removes/cancels the meeting.
    (location, start, end, title, description). Never create a duplicate.

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
_GOOGLE_EVENTS_LIST = _GOOGLE_EVENTS_INSERT
_GOOGLE_EVENTS_PATCH = _GOOGLE_EVENTS_INSERT + "/{event_id}"
_GOOGLE_EVENTS_DELETE = _GOOGLE_EVENTS_PATCH
_AGENT_SEARCH_PAST_HOURS = 24 * 7
_AGENT_SEARCH_FUTURE_HOURS = 24 * 30


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

    Also accepts ``events`` / ``meetings`` as a list for batch create
    (e.g. "schedule three meetings tomorrow").
    """
    from scheduling.views_agent import _resolve_restaurant_for_agent

    restaurant, acting_user, err = _resolve_restaurant_for_agent(request)
    if err:
        return Response({"success": False, "error": err["error"]}, status=err["status"])

    data = request.data if isinstance(getattr(request, "data", None), dict) else {}

    batch = data.get("events") or data.get("meetings") or data.get("appointments")
    if isinstance(batch, list) and batch:
        results = []
        errors = []
        for i, item in enumerate(batch[:20]):
            if not isinstance(item, dict):
                errors.append({"index": i, "error": "invalid_item"})
                continue
            merged = {**data, **item}
            merged.pop("events", None)
            merged.pop("meetings", None)
            merged.pop("appointments", None)
            one = _create_single_calendar_event(restaurant, merged)
            if one.get("success"):
                results.append(one)
            else:
                errors.append({"index": i, **{k: one.get(k) for k in ("error", "message_for_user")}})
        if not results:
            first_err = errors[0] if errors else {}
            return Response(
                {
                    "success": False,
                    "error": first_err.get("error") or "batch_failed",
                    "message_for_user": first_err.get("message_for_user")
                    or "Couldn't create those meetings.",
                    "errors": errors,
                },
                status=status.HTTP_400_BAD_REQUEST
                if first_err.get("error") != "calendar_not_connected"
                else status.HTTP_412_PRECONDITION_FAILED,
            )
        titles = [r.get("calendar_event", {}).get("summary") or "meeting" for r in results]
        return Response(
            {
                "success": True,
                "created_count": len(results),
                "events": results,
                "errors": errors,
                "event_id": results[0].get("event_id"),
                "message_for_user": (
                    f"📅 Created {len(results)} meeting"
                    f"{'s' if len(results) != 1 else ''}: "
                    + ", ".join(f'"{t}"' for t in titles[:5])
                    + ("…" if len(titles) > 5 else "")
                    + "."
                ),
            }
        )

    one = _create_single_calendar_event(restaurant, data)
    http_status = status.HTTP_200_OK
    if not one.get("success"):
        if one.get("error") == "calendar_not_connected":
            http_status = status.HTTP_412_PRECONDITION_FAILED
        elif one.get("error") in ("Missing title",) or str(one.get("error", "")).startswith(
            "Invalid"
        ):
            http_status = status.HTTP_400_BAD_REQUEST
        else:
            http_status = status.HTTP_502_BAD_GATEWAY
    return Response(one, status=http_status)


@api_view(["GET"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_list_calendar_events(request):
    """Search tenant Google Calendar events by keyword (for update/reschedule flows)."""
    from scheduling.views_agent import _resolve_restaurant_for_agent

    restaurant, acting_user, err = _resolve_restaurant_for_agent(request)
    if err:
        return Response({"success": False, "error": err["error"]}, status=err["status"])

    q = (
        request.query_params.get("q")
        or request.query_params.get("query")
        or request.query_params.get("title")
        or ""
    ).strip()
    if len(q) < 2:
        return Response(
            {
                "success": False,
                "error": "Query too short",
                "message_for_user": "Tell me the meeting name or who it's with (e.g. 'Loubna').",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    matches, list_err = _search_calendar_events(restaurant, q, acting_user=acting_user)
    if list_err:
        http_status = status.HTTP_412_PRECONDITION_FAILED
        if list_err.get("error") != "calendar_not_connected":
            http_status = status.HTTP_502_BAD_GATEWAY
        return Response({"success": False, **list_err}, status=http_status)

    message = _format_calendar_search_reply(matches, q=q)
    return Response(
        {
            "success": True,
            "query": q,
            "count": len(matches),
            "events": matches,
            "message_for_user": message,
        }
    )


@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_update_calendar_event(request):
    """Patch an existing Google Calendar event (location, time, title, etc.)."""
    from scheduling.views_agent import _resolve_restaurant_for_agent

    restaurant, acting_user, err = _resolve_restaurant_for_agent(request)
    if err:
        return Response({"success": False, "error": err["error"]}, status=err["status"])

    data = request.data if isinstance(getattr(request, "data", None), dict) else {}
    one = _update_single_calendar_event(restaurant, data, acting_user=acting_user)
    http_status = status.HTTP_200_OK
    if not one.get("success"):
        if one.get("error") == "calendar_not_connected":
            http_status = status.HTTP_412_PRECONDITION_FAILED
        elif one.get("error") in ("missing_event", "ambiguous_event", "Missing title"):
            http_status = status.HTTP_400_BAD_REQUEST
        else:
            http_status = status.HTTP_502_BAD_GATEWAY
    return Response(one, status=http_status)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_delete_calendar_event(request):
    """Delete/cancel an existing Google Calendar event."""
    from scheduling.views_agent import _resolve_restaurant_for_agent

    restaurant, acting_user, err = _resolve_restaurant_for_agent(request)
    if err:
        return Response({"success": False, "error": err["error"]}, status=err["status"])

    data = request.data if isinstance(getattr(request, "data", None), dict) else {}
    one = _delete_single_calendar_event(restaurant, data, acting_user=acting_user)
    http_status = status.HTTP_200_OK
    if not one.get("success"):
        if one.get("error") == "calendar_not_connected":
            http_status = status.HTTP_412_PRECONDITION_FAILED
        elif one.get("error") in ("missing_event", "ambiguous_event"):
            http_status = status.HTTP_400_BAD_REQUEST
        else:
            http_status = status.HTTP_502_BAD_GATEWAY
    return Response(one, status=http_status)


def _resolve_calendar_event_id(
    restaurant,
    data: dict,
    *,
    acting_user=None,
) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]] | None]:
    """Resolve event_id from payload or keyword search. Returns (event_id, error_dict, matches)."""
    event_id = (data.get("event_id") or data.get("eventId") or data.get("id") or "").strip()
    q = (data.get("q") or data.get("query") or data.get("title") or "").strip()

    if not event_id and q:
        matches, list_err = _search_calendar_events(restaurant, q, acting_user=acting_user)
        if list_err:
            return "", list_err, None
        if not matches:
            return "", {
                "success": False,
                "error": "missing_event",
                "message_for_user": f'I could not find a meeting matching "{q}".',
            }, None
        if len(matches) > 1:
            return "", {
                "success": False,
                "error": "ambiguous_event",
                "matches": matches,
                "message_for_user": _format_calendar_search_reply(matches, q=q),
            }, matches
        event_id = matches[0].get("id") or ""

    if not event_id:
        return "", {
            "success": False,
            "error": "missing_event",
            "message_for_user": (
                "I need the meeting to remove — give me who it's with or call "
                "list_calendar_events first, then delete_calendar_event with event_id."
            ),
        }, None

    return event_id, None, None


def _delete_single_calendar_event(
    restaurant,
    data: dict,
    *,
    acting_user=None,
) -> dict[str, Any]:
    event_id, resolve_err, _matches = _resolve_calendar_event_id(
        restaurant, data, acting_user=acting_user
    )
    if resolve_err:
        return resolve_err

    access_token, _gcal = _get_valid_access_token(restaurant)
    if not access_token:
        return {
            "success": False,
            "error": "calendar_not_connected",
            "connected": False,
            "connect_url": "/dashboard/settings?tab=integrations#google-calendar",
            "message_for_user": (
                "Google Calendar isn't connected — connect it in Settings → Integrations first."
            ),
        }

    summary = ""
    when_display = ""
    try:
        gr = requests.get(
            _GOOGLE_EVENTS_PATCH.format(event_id=event_id),
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if gr.status_code == 200:
            event = gr.json() or {}
            summary = (event.get("summary") or "").strip()
            start_raw = (event.get("start") or {}).get("dateTime") or (event.get("start") or {}).get("date") or ""
            if start_raw:
                when_display = (
                    start_raw.replace("T", " ").split("+")[0][:16]
                    if "T" in start_raw
                    else start_raw
                )
    except requests.RequestException:
        logger.warning("Could not fetch calendar event before delete event=%s", event_id)

    try:
        r = requests.delete(
            _GOOGLE_EVENTS_DELETE.format(event_id=event_id),
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
    except requests.RequestException as exc:
        logger.exception("Google Calendar delete failed restaurant=%s event=%s", restaurant.id, event_id)
        return {
            "success": False,
            "error": str(exc),
            "message_for_user": "Couldn't reach Google Calendar — try again shortly.",
        }

    if r.status_code >= 400:
        logger.warning(
            "Google Calendar delete returned %s restaurant=%s event=%s: %s",
            r.status_code,
            restaurant.id,
            event_id,
            r.text[:300],
        )
        return {
            "success": False,
            "error": "google_api_error",
            "status_code": r.status_code,
            "detail": r.text[:300],
            "message_for_user": "Google Calendar couldn't remove that meeting — check it still exists.",
        }

    label = summary or "meeting"
    msg = f'🗑️ Removed "{label}"'
    if when_display:
        msg += f" ({when_display})"
    msg += " from your calendar."

    return {
        "success": True,
        "event_id": event_id,
        "deleted_title": summary or None,
        "message_for_user": msg,
    }


def _calendar_search_tokens(raw_q: str) -> list[str]:
    import re

    stop = {
        "avec", "with", "the", "le", "la", "les", "un", "une", "meeting", "meetings",
        "rendez", "vous", "rendezvous", "update", "change", "move", "mettre", "jour",
    }
    tokens = [t for t in re.split(r"\W+", (raw_q or "").lower()) if len(t) >= 3 and t not in stop]
    if tokens:
        return tokens
    return [t for t in re.split(r"\W+", (raw_q or "").lower()) if len(t) >= 2 and t not in stop]


def _event_matches_query(row: dict[str, Any], *, q_lower: str, tokens: list[str]) -> bool:
    hay = " ".join(
        filter(
            None,
            [
                row.get("title") or "",
                row.get("location") or "",
                row.get("description") or "",
            ],
        )
    ).lower()
    if q_lower and q_lower in hay:
        return True
    if not tokens:
        return False
    hits = sum(1 for token in tokens if token in hay)
    if len(tokens) == 1:
        return hits >= 1
    return hits >= min(2, len(tokens))


def _fetch_calendar_events_for_agent(
    access_token: str,
    acting_user,
    *,
    past_hours: int = _AGENT_SEARCH_PAST_HOURS,
    future_hours: int = _AGENT_SEARCH_FUTURE_HOURS,
    max_results: int = 100,
) -> list[dict[str, Any]] | None:
    """Fetch calendar rows for agent search (wider horizon than dashboard widget)."""
    from dashboard.api.meetings_reminders import _serialize_event

    now = dj_timezone.now()
    time_min = (now - timedelta(hours=past_hours)).isoformat()
    time_max = (now + timedelta(hours=future_hours)).isoformat()
    try:
        res = requests.get(
            _GOOGLE_EVENTS_LIST,
            headers={"Authorization": f"Bearer {access_token}"},
            params={
                "timeMin": time_min,
                "timeMax": time_max,
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": str(max_results),
            },
            timeout=10,
        )
    except requests.RequestException as exc:
        logger.warning("Google Calendar events.list (agent) failed: %s", exc)
        return []

    if res.status_code == 401:
        return None
    if res.status_code != 200:
        logger.warning(
            "Google Calendar events.list (agent) non-200: %s %s",
            res.status_code,
            res.text[:200],
        )
        return []

    user_email = (getattr(acting_user, "email", None) or "").strip() or None
    out: list[dict[str, Any]] = []
    for ev in (res.json() or {}).get("items") or []:
        row = _serialize_event(ev, user_email, now)
        if row is None:
            continue
        row["description"] = (ev.get("description") or "")[:300]
        out.append(row)
    return out


def _search_calendar_events(
    restaurant,
    q: str,
    *,
    acting_user=None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    access_token, _gcal = _get_valid_access_token(restaurant)
    if not access_token:
        return [], {
            "error": "calendar_not_connected",
            "connected": False,
            "connect_url": "/dashboard/settings?tab=integrations#google-calendar",
            "message_for_user": (
                "Google Calendar isn't connected yet — connect it in Settings → Integrations "
                "so I can find and update meetings."
            ),
        }

    rows = _fetch_calendar_events_for_agent(access_token, acting_user)
    if rows is None:
        return [], {
            "error": "google_auth_failed",
            "message_for_user": "I couldn't reach Google Calendar — try reconnecting it in Settings.",
        }

    q_lower = q.lower()
    tokens = _calendar_search_tokens(q)
    matches = [row for row in rows if _event_matches_query(row, q_lower=q_lower, tokens=tokens)]
    matches.sort(key=lambda r: r.get("start") or "")
    return matches[:10], None


def _format_calendar_search_reply(matches: list[dict[str, Any]], *, q: str = "") -> str:
    if not matches:
        return f'No meetings found matching "{q}".'
    if len(matches) == 1:
        row = matches[0]
        when = (row.get("start") or "").replace("T", " ")[:16]
        loc = row.get("location") or "no location set"
        return (
            f'Found "{row.get("title")}" on {when} at {loc}. '
            "Use event_id with update_calendar_event to change it or delete_calendar_event to remove it."
        )
    lines = []
    for row in matches[:5]:
        when = (row.get("start") or "").replace("T", " ")[:16]
        loc = row.get("location") or "—"
        lines.append(f'• {row.get("title")} ({when}, {loc})')
    suffix = f" (+{len(matches) - 5} more)" if len(matches) > 5 else ""
    return (
        f'Found {len(matches)} meetings matching "{q}":\n'
        + "\n".join(lines)
        + suffix
        + "\nUse list result event_id with update_calendar_event or delete_calendar_event."
    )


def _update_single_calendar_event(
    restaurant,
    data: dict,
    *,
    acting_user=None,
) -> dict[str, Any]:
    event_id, resolve_err, _matches = _resolve_calendar_event_id(
        restaurant, data, acting_user=acting_user
    )
    if resolve_err:
        if resolve_err.get("error") == "missing_event":
            resolve_err["message_for_user"] = (
                "I need the meeting to update — give me who it's with or call "
                "list_calendar_events first, then update_calendar_event with event_id."
            )
        return resolve_err

    fallback_tz = (
        str(data.get("timezone") or "")
        or getattr(restaurant, "timezone", None)
        or "UTC"
    )

    patch: dict[str, Any] = {}
    title = data.get("title") or data.get("summary")
    if title is not None and str(title).strip():
        patch["summary"] = str(title).strip()[:1024]

    if "location" in data or "place" in data or "venue" in data:
        location = str(data.get("location") or data.get("place") or data.get("venue") or "").strip()
        patch["location"] = location[:1024]

    if "description" in data or "notes" in data:
        description = str(data.get("description") or data.get("notes") or "").strip()
        patch["description"] = description[:8000]

    raw_start = data.get("start") or data.get("start_at") or data.get("startTime")
    if raw_start:
        start_obj, _is_all_day, time_err = _coerce_event_time(raw_start, fallback_tz)
        if time_err:
            return {
                "success": False,
                "error": f"Invalid start time: {time_err}",
                "message_for_user": "I couldn't read the new start time.",
            }
        patch["start"] = start_obj

    raw_end = data.get("end") or data.get("end_at") or data.get("endTime")
    if raw_end:
        end_obj, _is_all_day, end_err = _coerce_event_time(raw_end, fallback_tz)
        if end_err:
            return {
                "success": False,
                "error": f"Invalid end time: {end_err}",
                "message_for_user": "I couldn't read the new end time.",
            }
        patch["end"] = end_obj

    if not patch:
        return {
            "success": False,
            "error": "nothing_to_update",
            "message_for_user": "Tell me what to change — location, time, or title.",
        }

    access_token, gcal = _get_valid_access_token(restaurant)
    if not access_token:
        return {
            "success": False,
            "error": "calendar_not_connected",
            "connected": False,
            "connect_url": "/dashboard/settings?tab=integrations#google-calendar",
            "message_for_user": (
                "Google Calendar isn't connected — connect it in Settings → Integrations first."
            ),
        }

    try:
        r = requests.patch(
            _GOOGLE_EVENTS_PATCH.format(event_id=event_id),
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=patch,
            timeout=10,
        )
    except requests.RequestException as exc:
        logger.exception("Google Calendar patch failed restaurant=%s event=%s", restaurant.id, event_id)
        return {
            "success": False,
            "error": str(exc),
            "message_for_user": "Couldn't reach Google Calendar — try again shortly.",
        }

    if r.status_code >= 400:
        logger.warning(
            "Google Calendar patch returned %s restaurant=%s event=%s: %s",
            r.status_code,
            restaurant.id,
            event_id,
            r.text[:300],
        )
        return {
            "success": False,
            "error": "google_api_error",
            "status_code": r.status_code,
            "detail": r.text[:300],
            "message_for_user": "Google Calendar couldn't update that meeting — check the event still exists.",
        }

    event = r.json() or {}
    summary = event.get("summary") or patch.get("summary") or "meeting"
    when_display = ""
    start_raw = (event.get("start") or {}).get("dateTime") or (event.get("start") or {}).get("date") or ""
    if start_raw:
        when_display = (
            start_raw.replace("T", " ").split("+")[0][:16]
            if "T" in start_raw
            else start_raw
        )
    loc = (event.get("location") or "").strip()
    changed = []
    if "location" in patch:
        changed.append("location")
    if "start" in patch or "end" in patch:
        changed.append("time")
    if "summary" in patch:
        changed.append("title")
    if "description" in patch:
        changed.append("details")

    msg = f'📅 Updated "{summary}"'
    if when_display:
        msg += f" ({when_display})"
    if loc:
        msg += f" — {loc}"
    msg += "."
    if event.get("htmlLink"):
        msg += f" {event['htmlLink']}"

    return {
        "success": True,
        "event_id": event.get("id") or event_id,
        "html_link": event.get("htmlLink"),
        "calendar_event": {
            "id": event.get("id") or event_id,
            "summary": event.get("summary"),
            "start": event.get("start"),
            "end": event.get("end"),
            "location": event.get("location"),
            "html_link": event.get("htmlLink"),
        },
        "updated_fields": changed,
        "message_for_user": msg,
    }


def _create_single_calendar_event(restaurant, data: dict) -> dict[str, Any]:
    """Shared create path for one event; returns a response body dict."""
    title = str(data.get("title") or data.get("summary") or "").strip()
    if not title:
        return {
            "success": False,
            "error": "Missing title",
            "message_for_user": "I need a title for the event.",
        }

    raw_start = data.get("start") or data.get("start_at") or data.get("startTime")
    raw_end = data.get("end") or data.get("end_at") or data.get("endTime")
    fallback_tz = (
        str(data.get("timezone") or "")
        or getattr(restaurant, "timezone", None)
        or "UTC"
    )

    start_obj, is_all_day, time_err = _coerce_event_time(raw_start, fallback_tz)
    if time_err:
        return {
            "success": False,
            "error": f"Invalid start time: {time_err}",
            "message_for_user": "I couldn't read the start time. Try '2026-05-15 14:30'.",
        }

    if raw_end:
        end_obj, _is_all_day_end, end_err = _coerce_event_time(raw_end, fallback_tz)
        if end_err:
            return {
                "success": False,
                "error": f"Invalid end time: {end_err}",
                "message_for_user": "I couldn't read the end time.",
            }
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
        return {
            "success": False,
            "error": "calendar_not_connected",
            "connected": False,
            "connect_url": "/dashboard/settings?tab=integrations#google-calendar",
            "message_for_user": (
                "I can't create that yet — Google Calendar isn't connected for "
                f"{restaurant.name}. Connect it from Settings → Integrations and "
                "I'll be able to schedule events directly."
            ),
        }

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
        return {
            "success": False,
            "error": str(exc),
            "message_for_user": "Couldn't reach Google Calendar — try again in a minute.",
        }

    if r.status_code >= 400:
        logger.warning(
            "Google Calendar insert returned %s for restaurant=%s: %s",
            r.status_code, restaurant.id, r.text[:300],
        )
        return {
            "success": False,
            "error": "google_api_error",
            "status_code": r.status_code,
            "detail": r.text[:300],
            "message_for_user": "Google Calendar rejected the event. Check the time and try again.",
        }

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

    return {
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
    }
