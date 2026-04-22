"""
Meetings & Reminders widget endpoint.

Pulls upcoming events from the tenant owner's Google Calendar (connected
during onboarding, see `accounts.views_onboarding.OnboardingGoogleCalendarView`)
and returns a lightweight shape the dashboard widget can render without a
second round-trip.

Why the tokens live on ``Restaurant.general_settings['google_calendar']``:
During onboarding the owner/admin connects their calendar and we persist
``access_token`` + ``refresh_token`` + ``token_expires_at`` on the tenant.
The widget reuses those tokens for any user in the tenant — the calendar
is effectively the "shared organisational calendar" for the business. If
the refresh token is missing or Google revoked the grant we degrade
gracefully and return ``{"connected": false, "items": []}`` so the widget
can render a "Connect Google Calendar" call-to-action instead of erroring.

Status mapping (widget pill vocabulary):
- ``URGENT``   — event starts within the next 30 min OR the event colorId
                 is 11 (tomato) / the word "urgent" is in the title.
- ``PENDING``  — event starts in the future within the horizon.
- ``DONE``     — event ended in the last 24 h (so the widget still shows
                 the win, matching the Tasks & Demands "Completed" lane).

The "who" label (``owner_label``):
- ``"Me"`` when the authenticated user's email matches the event's organizer.
- Otherwise the organizer's display name, truncated to a first name
  (e.g. "Wadi" not "Wadi Al-Hatem") to match the design spec.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone as py_timezone
from typing import Any

from django.utils import timezone as dj_timezone
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from core.http_caching import json_response_with_cache


logger = logging.getLogger(__name__)


# How far ahead we fetch. Keep small — the widget only displays 4–6 rows.
_HORIZON_HOURS = 36
# Also include things ended in the last 24 h so the widget shows "Done"
# rows alongside upcoming ones, mirroring the Tasks & Demands card.
_PAST_LOOKBACK_HOURS = 24
# Google cap 2500/day per calendar is far above what this widget polls.
_GOOGLE_EVENTS_MAX_RESULTS = 25


def _load_gcal_settings(restaurant) -> dict[str, Any]:
    gs = dict(getattr(restaurant, "general_settings", None) or {})
    return dict(gs.get("google_calendar") or {})


def _save_gcal_settings(restaurant, gcal: dict[str, Any]) -> None:
    """Persist refreshed tokens back on the restaurant. Uses update_fields
    so we don't stomp on any concurrent writes from the onboarding flow."""
    gs = dict(getattr(restaurant, "general_settings", None) or {})
    gs["google_calendar"] = gcal
    restaurant.general_settings = gs
    restaurant.save(update_fields=["general_settings"])


def _refresh_access_token(gcal: dict[str, Any]) -> dict[str, Any] | None:
    """Exchange the stored refresh_token for a new access_token.

    Returns the updated ``gcal`` dict on success, or ``None`` if the
    refresh failed (e.g. user revoked access, creds misconfigured).
    """
    import os

    import requests

    refresh_token = gcal.get("refresh_token")
    if not refresh_token:
        return None

    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    if not (client_id and client_secret):
        return None

    try:
        res = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=8,
        )
    except requests.RequestException as exc:
        logger.warning("Google token refresh failed: %s", exc)
        return None

    if res.status_code != 200:
        logger.warning(
            "Google token refresh non-200: %s %s",
            res.status_code, res.text[:200],
        )
        return None

    tokens = res.json() or {}
    new_access = tokens.get("access_token")
    if not new_access:
        return None

    expires_in = int(tokens.get("expires_in") or 3600)
    updated = dict(gcal)
    updated["access_token"] = new_access
    updated["token_expires_at"] = (
        dj_timezone.now() + timedelta(seconds=expires_in)
    ).isoformat()
    # Google sometimes rotates the refresh token; keep the new one if
    # returned, otherwise reuse the old one (the common case).
    if tokens.get("refresh_token"):
        updated["refresh_token"] = tokens["refresh_token"]
    return updated


def _get_valid_access_token(restaurant) -> tuple[str | None, dict[str, Any]]:
    """Return a fresh access token for this tenant's calendar.

    Auto-refreshes if the stored token has expired (or will within 60 s).
    Persists the refreshed token back on the restaurant.
    """
    gcal = _load_gcal_settings(restaurant)
    if not gcal.get("connected") or not gcal.get("access_token"):
        return None, gcal

    expires_at_raw = gcal.get("token_expires_at")
    expires_at: datetime | None = None
    if expires_at_raw:
        try:
            expires_at = datetime.fromisoformat(expires_at_raw)
        except (TypeError, ValueError):
            expires_at = None

    needs_refresh = (
        expires_at is None
        or expires_at <= dj_timezone.now() + timedelta(seconds=60)
    )
    if not needs_refresh:
        return gcal["access_token"], gcal

    refreshed = _refresh_access_token(gcal)
    if not refreshed:
        return None, gcal

    _save_gcal_settings(restaurant, refreshed)
    return refreshed["access_token"], refreshed


def _parse_iso_any(raw: str | None) -> datetime | None:
    """Parse either a full RFC3339 datetime or a YYYY-MM-DD all-day date.

    Google Calendar events use ``start.dateTime`` for timed events and
    ``start.date`` for all-day events; we normalize both into a UTC
    datetime so the widget can sort them uniformly.
    """
    if not raw:
        return None
    try:
        if "T" in raw:
            # RFC3339 — may end with "Z". Python 3.11 accepts "Z" in
            # fromisoformat, but guard for older runtimes just in case.
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            return datetime.fromisoformat(raw)
        # All-day "YYYY-MM-DD". Pin at midnight UTC so it sorts with
        # timed events from the same day.
        return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=py_timezone.utc)
    except (TypeError, ValueError):
        return None


_URGENT_KEYWORD_RE = re.compile(
    r"\b(urgent|asap|critical|immediate(ly)?)\b", re.IGNORECASE,
)


def _derive_status(
    start: datetime | None,
    end: datetime | None,
    title: str,
    color_id: str | None,
    now: datetime,
) -> str:
    """Map a calendar event to the widget status vocabulary."""
    if end and end <= now and (now - end) <= timedelta(hours=_PAST_LOOKBACK_HOURS):
        return "DONE"
    if start:
        delta = start - now
        if delta <= timedelta(minutes=30) and delta >= timedelta(0):
            return "URGENT"
    # Google's color 11 is "Tomato" — folks use it for red/critical.
    if color_id == "11":
        return "URGENT"
    if title and _URGENT_KEYWORD_RE.search(title):
        return "URGENT"
    return "PENDING"


def _short_name(full_name: str | None, email: str | None) -> str:
    """Match the mock: use first-name-only labels like "Wadi", "Me"."""
    if full_name:
        first = str(full_name).strip().split()[0]
        if first:
            return first
    if email:
        # Best-effort: email local part, dots stripped.
        local = str(email).split("@", 1)[0]
        return local.split(".", 1)[0].capitalize() if local else ""
    return ""


def _serialize_event(
    ev: dict[str, Any],
    user_email: str | None,
    now: datetime,
) -> dict[str, Any] | None:
    """Transform a Google Calendar event into the widget row shape.

    Returns ``None`` for events we shouldn't surface (cancelled, no start).
    """
    if ev.get("status") == "cancelled":
        return None

    start_raw = (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date")
    end_raw = (ev.get("end") or {}).get("dateTime") or (ev.get("end") or {}).get("date")
    start = _parse_iso_any(start_raw)
    end = _parse_iso_any(end_raw)
    if not start:
        return None

    title = (ev.get("summary") or "(no title)").strip()
    color_id = ev.get("colorId")

    organizer = ev.get("organizer") or {}
    creator = ev.get("creator") or {}
    organizer_email = (organizer.get("email") or creator.get("email") or "").strip().lower()
    organizer_name = organizer.get("displayName") or creator.get("displayName")

    if user_email and organizer_email and organizer_email == user_email.strip().lower():
        owner_label = "Me"
    elif organizer.get("self") or creator.get("self"):
        owner_label = "Me"
    else:
        owner_label = _short_name(organizer_name, organizer_email) or "—"

    status = _derive_status(start, end, title, color_id, now)

    # Attendee preview — shown on the row as a tooltip / aria label.
    attendees = ev.get("attendees") or []
    attendee_count = sum(1 for a in attendees if not a.get("self"))

    return {
        "id": ev.get("id"),
        "title": title,
        "start": start.isoformat(),
        "end": end.isoformat() if end else None,
        "all_day": bool((ev.get("start") or {}).get("date") and not (ev.get("start") or {}).get("dateTime")),
        "owner_label": owner_label,
        "owner_is_me": owner_label == "Me",
        "status": status,
        "html_link": ev.get("htmlLink") or None,
        "hangout_link": ev.get("hangoutLink") or None,
        "location": (ev.get("location") or "").strip() or None,
        "attendee_count": attendee_count,
        "calendar_id": "primary",
    }


class MeetingsRemindersView(APIView):
    """
    GET /api/dashboard/meetings-reminders/?limit=6

    Response (connected):
        {
          "connected": true,
          "email": "owner@example.com",
          "items": [MeetingRemindersItem, ...],
          "counts": {"urgent": N, "pending": N, "done": N},
          "calendar_link": "https://calendar.google.com/calendar/u/0/r",
          "generated_at": "...",
        }

    Response (not connected):
        {
          "connected": false,
          "email": null,
          "items": [],
          "counts": {"urgent": 0, "pending": 0, "done": 0},
          "calendar_link": "https://calendar.google.com/",
          "configured": <bool>,  # whether the server has GCal creds
          "generated_at": "...",
        }
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        restaurant = getattr(request.user, "restaurant", None)
        now = dj_timezone.now()

        # Default payload used when we're not connected, or anything goes
        # wrong downstream. The widget renders a "Connect calendar" CTA
        # off this shape without any error handling of its own.
        not_connected_payload: dict[str, Any] = {
            "connected": False,
            "email": None,
            "items": [],
            "counts": {"urgent": 0, "pending": 0, "done": 0},
            "calendar_link": "https://calendar.google.com/",
            "configured": self._server_configured(),
            "generated_at": now.isoformat(),
        }

        if not restaurant:
            return json_response_with_cache(
                request, not_connected_payload,
                max_age=30, private=True, stale_while_revalidate=60,
            )

        access_token, gcal = _get_valid_access_token(restaurant)
        if not access_token:
            not_connected_payload["email"] = gcal.get("email")
            return json_response_with_cache(
                request, not_connected_payload,
                max_age=30, private=True, stale_while_revalidate=60,
            )

        try:
            limit = int(request.query_params.get("limit") or 6)
        except (TypeError, ValueError):
            limit = 6
        limit = max(1, min(limit, 20))

        items = self._fetch_events(access_token, request.user, now)
        if items is None:
            # Token likely invalid — force a refresh next call by
            # clearing the expiry. Return the not-connected shape so
            # the widget shows the reconnect CTA.
            cleared = dict(gcal)
            cleared["token_expires_at"] = "1970-01-01T00:00:00+00:00"
            _save_gcal_settings(restaurant, cleared)
            not_connected_payload["email"] = gcal.get("email")
            return json_response_with_cache(
                request, not_connected_payload,
                max_age=10, private=True, stale_while_revalidate=30,
            )

        # Order: URGENT first, then upcoming PENDING by start asc,
        # then DONE (recent first) at the bottom.
        def _rank(row: dict[str, Any]) -> tuple:
            status_rank = {"URGENT": 0, "PENDING": 1, "DONE": 2}.get(row["status"], 3)
            return (status_rank, row.get("start") or "")

        items.sort(key=_rank)
        items = items[:limit]

        counts = {"urgent": 0, "pending": 0, "done": 0}
        for it in items:
            k = it["status"].lower()
            if k in counts:
                counts[k] += 1

        data = {
            "connected": True,
            "email": gcal.get("email"),
            "items": items,
            "counts": counts,
            "calendar_link": "https://calendar.google.com/calendar/u/0/r",
            "generated_at": now.isoformat(),
        }
        return json_response_with_cache(
            request, data,
            # 60 s cache matches the widget's polling cadence. Google
            # Calendar events don't change every second, and the ETag
            # short-circuit keeps bandwidth use low.
            max_age=60, private=True, stale_while_revalidate=120,
        )

    @staticmethod
    def _server_configured() -> bool:
        import os
        return bool(
            os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
            and os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
        )

    def _fetch_events(
        self,
        access_token: str,
        user,
        now: datetime,
    ) -> list[dict[str, Any]] | None:
        """Call Google Calendar v3 events.list on the primary calendar.

        Returns a list of serialized rows on success, ``[]`` if the
        call succeeded with no events, or ``None`` on auth failure so
        the caller can blank out the token and prompt reconnect.
        """
        import requests

        time_min = (now - timedelta(hours=_PAST_LOOKBACK_HOURS)).isoformat()
        time_max = (now + timedelta(hours=_HORIZON_HOURS)).isoformat()

        try:
            res = requests.get(
                "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "timeMin": time_min,
                    "timeMax": time_max,
                    "singleEvents": "true",
                    "orderBy": "startTime",
                    "maxResults": str(_GOOGLE_EVENTS_MAX_RESULTS),
                },
                timeout=8,
            )
        except requests.RequestException as exc:
            logger.warning("Google Calendar events.list failed: %s", exc)
            return []

        if res.status_code == 401:
            return None
        if res.status_code != 200:
            logger.warning(
                "Google Calendar events.list non-200: %s %s",
                res.status_code, res.text[:200],
            )
            return []

        raw_events = (res.json() or {}).get("items") or []
        user_email = (getattr(user, "email", None) or "").strip() or None

        out: list[dict[str, Any]] = []
        for ev in raw_events:
            row = _serialize_event(ev, user_email, now)
            if row is not None:
                out.append(row)
        return out
