"""
WhatsApp staff "when is my shift" / my schedule replies (Django-owned).

Keeps Lua/Space from inventing "trouble fetching your shift details".
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Optional

from django.db.models import Q
from django.utils import timezone

# shifts? / shit / shif typos — avoid sh(?:i[fpt]|it) which never matches "shift"
_SHIFT = r"(?:shifts?|shits?|shifs?|shiifts?)"

MY_SHIFTS_RE = re.compile(
    r"\b("
    rf"my\s+{_SHIFT}|my\s+schedule|"
    rf"when\s+(?:is|are|was)\s+my\s+(?:{_SHIFT}|work|schedule)|"
    rf"what(?:'s|\s+is|\s+are)\s+my\s+(?:{_SHIFT}|schedule|work)|"
    rf"what\s+time\s+(?:is\s+)?(?:my\s+)?(?:{_SHIFT}|work)|"
    r"when\s+do\s+i\s+work|"
    rf"{_SHIFT}\s+(?:today|tomorrow)|schedule\s+(?:today|tomorrow)|"
    r"do\s+i\s+(?:work|have\s+(?:a\s+)?shift)|"
    r"am\s+i\s+(?:working|scheduled)|"
    rf"horaire|mes\s+{_SHIFT}|mon\s+planning|"
    r"شيفت|دوامي|جدول"
    r")\b",
    re.I,
)

_SHIFT_TYPO_WORDS = frozenset({
    "shift", "shifts", "shit", "shif", "shiift", "work", "schedule",
    "duty", "rota", "service", "turn",
})


def _looks_like_shift_typo_query(text: str) -> bool:
    """Catch 'when is my shit today' and similar autocorrect typos."""
    m = re.search(
        r"\bwhen\s+(?:is|are)\s+my\s+(\w+)\s+(?:today|tomorrow|tonight)\b",
        text,
        re.I,
    )
    if m and m.group(1).lower() in _SHIFT_TYPO_WORDS:
        return True
    if re.search(
        r"\bwhat\s+time\s+(?:am\s+)?i\s+(?:working|on)\s+(?:today|tomorrow)\b",
        text,
        re.I,
    ):
        return True
    return False


def looks_like_my_shifts_query(text: str) -> bool:
    t = (text or "").strip()
    if not t or len(t) < 5:
        return False
    if MY_SHIFTS_RE.search(t):
        return True
    return _looks_like_shift_typo_query(t)


def _parse_shift_range(text: str):
    """Return (start_date, end_date) in local dates from natural language."""
    today = timezone.localdate()
    t = (text or "").lower()
    if re.search(r"\btoday\s+and\s+tomorrow\b|\btomorrow\s+and\s+today\b", t):
        return today, today + timedelta(days=1)
    if re.search(r"\btoday\b", t) and re.search(r"\btomorrow\b", t):
        return today, today + timedelta(days=1)
    if re.search(r"\btomorrow\b", t) and not re.search(r"\btoday\b", t):
        d = today + timedelta(days=1)
        return d, d
    if re.search(r"\btoday\b|\btonight\b|\bthis\s+evening\b", t):
        return today, today
    if re.search(r"\bthis\s+week\b", t):
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    if re.search(r"\bnext\s+week\b", t):
        start = today - timedelta(days=today.weekday()) + timedelta(days=7)
        return start, start + timedelta(days=6)
    # Default: today + tomorrow (covers "when is my shift today and tomorrow")
    return today, today + timedelta(days=1)


def _fmt_time(dt) -> str:
    if not dt:
        return "—"
    try:
        local = timezone.localtime(dt)
        return local.strftime("%H:%M")
    except Exception:
        return "—"


def format_whatsapp_my_shifts_reply(user, text: str) -> str:
    """
    Build a staff-facing WhatsApp message listing the user's shifts.
    """
    from scheduling.models import AssignedShift

    if not user:
        return "Please link your phone number in your profile to see your shifts."

    restaurant = getattr(user, "restaurant", None)
    range_start, range_end = _parse_shift_range(text)

    qs = AssignedShift.objects.filter(
        shift_date__gte=range_start,
        shift_date__lte=range_end,
    ).filter(Q(staff=user) | Q(staff_members=user))
    if restaurant:
        qs = qs.filter(schedule__restaurant=restaurant)
    qs = (
        qs.select_related("schedule", "location")
        .order_by("shift_date", "start_time")
        .distinct()
    )

    first = (getattr(user, "first_name", None) or "").strip() or "there"
    if not qs.exists():
        if range_start == range_end:
            day_label = "today" if range_start == timezone.localdate() else range_start.strftime("%a %d %b")
            return f"Hi {first} — you have no shifts scheduled for *{day_label}*."
        return (
            f"Hi {first} — you have no shifts from "
            f"*{range_start.strftime('%a %d %b')}* to *{range_end.strftime('%a %d %b')}*."
        )

    lines = [f"Hi {first} — here are your shifts:", ""]
    for s in qs:
        day = s.shift_date.strftime("%a %d %b") if s.shift_date else "—"
        start = _fmt_time(s.start_time)
        end = _fmt_time(s.end_time)
        role = (getattr(s, "role", None) or "").strip()
        loc = ""
        branch = getattr(s, "location", None)
        if branch and getattr(branch, "name", None):
            loc = f" · {branch.name}"
        elif getattr(s, "workspace_location", None):
            loc = f" · {s.workspace_location}"
        role_bit = f" ({role})" if role else ""
        lines.append(f"• *{day}* {start}–{end}{role_bit}{loc}")

    lines.append("")
    lines.append("Say *Clock me in* when you're at work and I'll ask for your location.")
    return "\n".join(lines)


def process_whatsapp_my_shifts(notification_service, user, phone_digits: str, raw_body: str) -> bool:
    """
    Reply with the staff member's shifts. Returns True when handled.
    """
    if not looks_like_my_shifts_query(raw_body or ""):
        return False
    try:
        reply = format_whatsapp_my_shifts_reply(user, raw_body or "")
    except Exception:
        import logging

        logging.getLogger(__name__).exception("WhatsApp my-shifts failed phone=%s", phone_digits)
        reply = (
            "I couldn't load your shifts just now. Please try again in a moment, "
            "or ask your manager to check the schedule."
        )
    notification_service.send_whatsapp_text(phone_digits, reply)
    return True
