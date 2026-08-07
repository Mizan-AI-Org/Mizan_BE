"""Detect when a manager wants a reminder/calendar/compliance action — not a dashboard task."""
from __future__ import annotations

import re

_MANAGER_ROLES = frozenset({"OWNER", "MANAGER", "ADMIN"})

_REMINDER_PHRASES = (
    r"\bremind\s+me\b",
    r"\brappel(?:le|er)?[\s-]*moi\b",
    r"\bdon'?t\s+forget\b",
    r"\bn'?oublie\s+pas\b",
    r"\bping\s+me\b",
    r"\bnotify\s+me\b",
    r"\bpréviens[\s-]*moi\b",
    r"\bprevien[\s-]*moi\b",
    r"\bme\s+rappeler\b",
    r"\bset\s+a\s+reminder\b",
    r"\bcreate\s+a\s+reminder\b",
    r"\badd\s+a\s+reminder\b",
    r"\bpersonal\s+reminder\b",
    r"\bdeadline\s+reminder\b",
)

_COMPLIANCE_PHRASES = (
    r"\binsur(?:ance)?\b",
    r"\bassurance\b",
    r"\bexpir(?:y|e|ation)\b",
    r"\brenouvel",
    r"\brenew\b",
    r"\bconformit",
    r"\bpermit\b",
    r"\blicen[cs]e\b",
    r"\bregistration\b",
    r"\bregistre\b",
    r"\bhygien",
    r"\bhaccp\b",
    r"\bextinguisher\b",
)

_CALENDAR_PHRASES = (
    r"\bmeeting\b",
    r"\bappointment\b",
    r"\brendez[\s-]*vous\b",
    r"\brdv\b",
    r"\bbirthday\b",
    r"\banniversaire\b",
    r"\bcalendar\b",
    r"\bagenda\b",
)

_STAFF_DELEGATION = (
    r"\btell\s+\w+\s+to\b",
    r"\bask\s+\w+\s+to\b",
    r"\bassign\s+\w+\b",
    r"\bdelegate\s+to\b",
    r"\bdemande\s+[àa]\s+\w+\s+de\b",
    r"\bdire\s+[àa]\s+\w+\s+de\b",
)


def is_manager_role(user) -> bool:
    return (getattr(user, "role", None) or "").upper() in _MANAGER_ROLES


def looks_like_manager_reminder_intent(text: str) -> bool:
    """True when the message is a self-reminder / compliance / calendar note — not staff work."""
    if not text or not str(text).strip():
        return False
    lower = str(text).lower().replace("'", "'")
    if any(re.search(p, lower) for p in _STAFF_DELEGATION):
        return False
    if any(re.search(p, lower) for p in _REMINDER_PHRASES):
        return True
    if any(re.search(p, lower) for p in _COMPLIANCE_PHRASES) and re.search(
        r"\b(remind|rappel|renew|renouvel|expir|before|avant)\b", lower
    ):
        return True
    if any(re.search(p, lower) for p in _CALENDAR_PHRASES) and not re.search(
        r"\b(tell|ask|assign|demande|dire)\s+\w+\s+(to|de)\b", lower
    ):
        return True
    return False


def manager_self_task_blocked_message(*, language: str = "en") -> str:
    from core.i18n import tr

    return tr("miya.manager_self_task_blocked", language or "en")
