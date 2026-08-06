"""WhatsApp copy for personal reminder nudges (Miya voice)."""
from __future__ import annotations

from django.utils import timezone


def days_until_due(rem) -> int | None:
    if not rem.due_at:
        return None
    today = timezone.localdate()
    return (rem.due_at.date() - today).days


def milestones_for_reminder(rem) -> list[int]:
    """Days-before-due milestones that trigger an approach ping."""
    ms = {30, 14, 7, 3, 1, 0}
    doc = getattr(rem, "linked_compliance_document", None)
    if doc and getattr(doc, "remind_days_before", None):
        ms.add(int(doc.remind_days_before))
    return sorted(ms, reverse=True)


def next_approach_milestone(rem) -> int | None:
    """Return the tightest milestone window we've entered but not pinged yet."""
    days_left = days_until_due(rem)
    if days_left is None:
        return None
    sent = {int(x) for x in (rem.approach_nudges_sent or []) if isinstance(x, (int, float))}
    if days_left < 0:
        return -1 if -1 not in sent else None
    applicable = [m for m in milestones_for_reminder(rem) if days_left <= m and m not in sent]
    if not applicable:
        return None
    return min(applicable)


def build_approach_message(rem, milestone: int) -> str:
    title = (rem.title or "Reminder").strip()
    due_s = rem.due_at.strftime("%b %d, %Y") if rem.due_at else ""
    doc = getattr(rem, "linked_compliance_document", None)
    doc_label = getattr(doc, "title", None) if doc else None
    label = doc_label or title
    days_left = days_until_due(rem)

    if milestone < 0 or (days_left is not None and days_left < 0):
        return (
            f"Hi — it's Miya. ⏰ *{label}* is past due ({due_s}). "
            "Please renew or update the expiry date in Mizan when you can."
        )
    if days_left == 0:
        return (
            f"Hi — it's Miya. ⏰ *{label}* is due *today* ({due_s}). "
            "Reply here if you've already renewed."
        )
    if days_left == 1:
        when = "tomorrow"
    elif days_left is not None:
        when = f"in {days_left} days"
    else:
        when = f"in {milestone} days"
    return (
        f"Hi — it's Miya. ⏰ Heads up: *{label}* is coming up {when} "
        f"(due {due_s}). I'll ping you again as the date gets closer."
    )


def build_due_message(rem) -> str:
    title = (rem.title or "Reminder").strip()
    lines = [f"Hi — it's Miya. ⏰ Reminder: *{title}*"]
    if rem.body:
        lines.append(rem.body.strip())
    if rem.due_at:
        lines.append(f"Due: {rem.due_at.strftime('%a %b %d, %Y %H:%M')}")
    return "\n".join(lines)
