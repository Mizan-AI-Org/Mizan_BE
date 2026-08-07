"""Preference + quiet-hours gating for proactive notifications."""
from __future__ import annotations

from datetime import datetime, time
from typing import Any

from django.utils import timezone


def load_notification_prefs(user) -> Any | None:
    try:
        from notifications.models import NotificationPreference

        prefs = getattr(user, "notification_preferences", None)
        if prefs is not None:
            return prefs
        return NotificationPreference.objects.filter(user=user).first()
    except Exception:
        return None


def wants_proactive_briefing(user) -> bool:
    """
    Respect notification preferences.
    - whatsapp_enabled False → no WA briefing
    - digest_enabled True is OK (briefing IS the digest)
    - No phone → cannot deliver WA
    """
    prefs = load_notification_prefs(user)
    if prefs is not None and prefs.whatsapp_enabled is False:
        return False
    phone = briefing_phone(user, prefs)
    return bool(phone and len(phone) >= 8)


def briefing_phone(user, prefs=None) -> str:
    prefs = prefs if prefs is not None else load_notification_prefs(user)
    raw = ""
    if prefs is not None:
        raw = (getattr(prefs, "whatsapp_number", None) or "") or ""
    if not raw:
        raw = getattr(user, "phone", None) or ""
    try:
        from staff.follow_up_helpers import normalize_phone

        return normalize_phone(str(raw)) or ""
    except Exception:
        digits = "".join(c for c in str(raw) if c.isdigit() or c == "+")
        return digits


def in_quiet_hours(user, *, now: datetime | None = None) -> bool:
    """True when proactive sends should be deferred (do not spam during quiet hours)."""
    prefs = load_notification_prefs(user)
    if prefs is None or not getattr(prefs, "quiet_hours_enabled", False):
        return False
    start: time | None = getattr(prefs, "quiet_hours_start", None)
    end: time | None = getattr(prefs, "quiet_hours_end", None)
    if not start or not end:
        return False
    local = timezone.localtime(now or timezone.now()).time()
    if start <= end:
        return start <= local <= end
    # Overnight window (e.g. 22:00–07:00)
    return local >= start or local <= end


def can_deliver_now(user, *, now: datetime | None = None) -> tuple[bool, str]:
    if not wants_proactive_briefing(user):
        return False, "prefs_or_phone"
    if in_quiet_hours(user, now=now):
        return False, "quiet_hours"
    return True, "ok"
