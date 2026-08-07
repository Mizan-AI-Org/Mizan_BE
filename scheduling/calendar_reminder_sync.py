"""Calendar event ↔ WhatsApp reminder sync and proactive meeting pings."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

GCAL_EVENT_MARKER = "gcal_event_id:"
# Minutes before start → cache milestone key (dedupe WhatsApp pings).
MEETING_APPROACH_MINUTES = (1440, 60, 30)


def gcal_event_id_from_body(body: str) -> str | None:
    if not body:
        return None
    m = re.search(r"gcal_event_id:([^\s\n]+)", body)
    return m.group(1).strip() if m else None


def _reminder_exists_for_gcal_event(restaurant_id, event_id: str) -> bool:
    from scheduling.memory_models import PersonalReminder

    needle = f"{GCAL_EVENT_MARKER}{event_id}"
    return PersonalReminder.objects.filter(
        restaurant_id=restaurant_id,
        status="pending",
        body__icontains=needle,
    ).exists()


def _parse_event_start(row: dict[str, Any]) -> datetime | None:
    raw = row.get("start") or ""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        return dt
    except (TypeError, ValueError):
        return None


def sync_calendar_event_reminder(
    *,
    restaurant,
    owner,
    event_id: str,
    title: str,
    start_at: datetime,
    location: str = "",
    is_reminder: bool = False,
    meeting_kind: str = "",
) -> dict[str, Any]:
    """
    Ensure a PersonalReminder exists for a Google Calendar event so Miya can
    ping the manager on WhatsApp (approach nudges + at start time).
    """
    from scheduling.memory_models import PersonalReminder

    summary = {"created": 0, "updated": 0, "skipped": 0}
    if not restaurant or not owner or not event_id or not title or not start_at:
        summary["skipped"] = 1
        return summary

    if timezone.is_naive(start_at):
        start_at = timezone.make_aware(start_at)

    phone = re.sub(r"\D", "", str(getattr(owner, "phone", "") or ""))
    loc_bit = f" Location: {location}." if location else ""
    kind_bit = f"\nmeeting_kind:{meeting_kind.strip().upper()}" if (meeting_kind or "").strip() else ""
    body = (
        f"{GCAL_EVENT_MARKER}{event_id}\n"
        f"{'Reminder' if is_reminder else 'Meeting'} on your Google Calendar.{loc_bit}"
        f"{kind_bit}"
    ).strip()

    needle = f"{GCAL_EVENT_MARKER}{event_id}"
    rem = (
        PersonalReminder.objects.filter(
            restaurant=restaurant,
            owner=owner,
            body__icontains=needle,
        )
        .order_by("-created_at")
        .first()
    )
    if rem:
        rem.title = title[:255]
        rem.body = body[:4000]
        rem.due_at = start_at
        rem.phone = phone[:40]
        rem.status = "pending"
        rem.save()
        summary["updated"] = 1
        return summary

    PersonalReminder.objects.create(
        restaurant=restaurant,
        owner=owner,
        phone=phone[:40],
        title=title[:255],
        body=body[:4000],
        due_at=start_at,
        timezone_name=str(getattr(restaurant, "timezone", None) or "Africa/Casablanca")[:64],
        approach_nudges_sent=[],
    )
    summary["created"] = 1
    return summary


def cancel_calendar_event_reminder(*, restaurant, event_id: str) -> dict[str, Any]:
    """Cancel PersonalReminders linked to a deleted Google Calendar event."""
    from scheduling.memory_models import PersonalReminder

    summary = {"cancelled": 0}
    if not restaurant or not event_id:
        return summary
    needle = f"{GCAL_EVENT_MARKER}{event_id}"
    updated = PersonalReminder.objects.filter(
        restaurant=restaurant,
        status="pending",
        body__icontains=needle,
    ).update(status="cancelled")
    summary["cancelled"] = updated
    return summary


def meeting_kind_from_text(*parts: str) -> str | None:
    """Detect department meeting kind from title/description."""
    blob = " ".join(p for p in parts if p).lower()
    if not blob:
        return None
    # Explicit marker first
    m = re.search(r"meeting_kind:([a-z_]+)", blob, re.I)
    if m:
        return normalize_meeting_kind(m.group(1))
    aliases = {
        "FOH": ("front of house", "foh", "front-of-house", "salle", "service"),
        "KITCHEN": ("kitchen", "cuisine", "back of house", "boh"),
        "MANAGER": ("manager meeting", "managers meeting", "management meeting", "managers' meeting"),
    }
    for kind, keys in aliases.items():
        if any(k in blob for k in keys):
            return kind
    return None


def normalize_meeting_kind(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).strip().upper().replace("-", "_").replace(" ", "_")
    mapping = {
        "FOH": "FOH",
        "FRONT_OF_HOUSE": "FOH",
        "FRONTOFHOUSE": "FOH",
        "KITCHEN": "KITCHEN",
        "CUISINE": "KITCHEN",
        "MANAGER": "MANAGER",
        "MANAGEMENT": "MANAGER",
        "MANAGERS": "MANAGER",
    }
    return mapping.get(s) or (s if s in ("FOH", "KITCHEN", "MANAGER") else None)


def title_with_meeting_kind(title: str, meeting_kind: str | None) -> str:
    kind = normalize_meeting_kind(meeting_kind)
    if not kind:
        return (title or "").strip()
    t = (title or "").strip()
    prefixes = {
        "FOH": "FOH meeting",
        "KITCHEN": "Kitchen meeting",
        "MANAGER": "Manager meeting",
    }
    label = prefixes[kind]
    low = t.lower()
    if any(p.lower() in low for p in prefixes.values()) or kind.lower() in low:
        return t[:255]
    if not t:
        return label
    return f"{label} — {t}"[:255]


def build_meeting_approach_message(*, title: str, start_at: datetime, minutes_before: int) -> str:
    when = start_at.strftime("%a %b %d, %H:%M")
    label = (title or "Meeting").strip()
    if minutes_before >= 1440:
        lead = "tomorrow" if minutes_before < 2880 else f"in {minutes_before // 1440} day(s)"
    elif minutes_before >= 60:
        lead = f"in {minutes_before // 60} hour(s)"
    else:
        lead = f"in {minutes_before} minutes"
    return (
        f"Hi — it's Miya. 📅 Heads up: *{label}* starts {lead} "
        f"({when}). Reply here if you need to reschedule."
    )


def _nudge_cache_key(restaurant_id, event_id: str, milestone: int) -> str:
    return f"gcal_approach:{restaurant_id}:{event_id}:{milestone}"


def calendar_event_approach_sweep_for_restaurant(restaurant) -> dict[str, int]:
    """
    Ping managers on WhatsApp before Google Calendar events (1 day, 1 hour, 30 min).
    Skips events already tracked via PersonalReminder (Miya-created sync path).
    """
    from accounts.models import CustomUser
    from dashboard.api.meetings_reminders import _get_valid_access_token
    from dashboard.api.calendar_write import _fetch_calendar_events_for_agent
    from notifications.services import notification_service

    summary = {"sent": 0, "skipped": 0, "checked": 0}
    access_token, _gcal = _get_valid_access_token(restaurant)
    if not access_token:
        return summary

    managers = list(
        CustomUser.objects.filter(
            restaurant=restaurant,
            role__in=["OWNER", "MANAGER", "ADMIN"],
            is_active=True,
        )
        .exclude(phone__isnull=True)
        .exclude(phone="")
    )
    if not managers:
        return summary

    now = timezone.now()
    for manager in managers:
        rows = _fetch_calendar_events_for_agent(
            access_token,
            manager,
            past_hours=1,
            future_hours=48,
            max_results=40,
        )
        if not rows:
            continue

        phone = re.sub(r"\D", "", str(getattr(manager, "phone", "") or ""))
        if len(phone) < 8:
            continue

        for row in rows:
            event_id = str(row.get("id") or "").strip()
            if not event_id:
                continue
            if _reminder_exists_for_gcal_event(restaurant.id, event_id):
                summary["skipped"] += 1
                continue

            start_at = _parse_event_start(row)
            if not start_at or start_at <= now:
                continue

            minutes_until = int((start_at - now).total_seconds() // 60)
            summary["checked"] += 1
            title = (row.get("title") or "Meeting").strip()

            for milestone in MEETING_APPROACH_MINUTES:
                if minutes_until > milestone or minutes_until < max(0, milestone - 20):
                    continue
                cache_key = _nudge_cache_key(restaurant.id, event_id, milestone)
                if cache.get(cache_key):
                    summary["skipped"] += 1
                    continue

                text = build_meeting_approach_message(
                    title=title,
                    start_at=start_at,
                    minutes_before=milestone,
                )
                try:
                    result = notification_service.send_whatsapp_text(phone, text)
                    ok = result[0] if isinstance(result, tuple) else bool(result)
                    if not ok:
                        summary["skipped"] += 1
                        continue
                except Exception:
                    logger.exception(
                        "calendar_event_approach failed restaurant=%s event=%s",
                        restaurant.id,
                        event_id,
                    )
                    summary["skipped"] += 1
                    continue

                cache.set(cache_key, True, timeout=60 * 60 * 24 * 8)
                summary["sent"] += 1

    return summary
