"""Format Daily Operations Intelligence briefings for WhatsApp / dashboard."""
from __future__ import annotations

from miya.services.intelligence.proactive.types import (
    CATEGORY_EMOJI,
    AttentionCategory,
    DailyBriefing,
)


def format_daily_briefing(briefing: DailyBriefing, *, manager_name: str = "") -> str:
    """
    Example shape:

    Good morning.

    Here's what needs your attention today:

    🔴 2 unresolved incidents
    🟠 4 overdue tasks
    ...
    Want me to handle any of these?
    """
    name = (manager_name or "").strip()
    greet = f"Good morning{', ' + name.split()[0] if name else ''}."
    if briefing.period == "evening":
        greet = f"Evening check-in{', ' + name.split()[0] if name else ''}."

    if not briefing.items:
        return (
            f"{greet}\n\n"
            "Nothing urgent needs your attention right now — you're clear.\n"
            "Ask me anytime: *where are we at?*"
        )

    lines = [
        greet,
        "",
        "Here's what needs your attention today:"
        if briefing.period != "evening"
        else "Here's what still needs attention:",
        "",
    ]
    for item in briefing.items:
        emoji = CATEGORY_EMOJI.get(item.category, "•")
        # Prefer human title already composed by scanner
        lines.append(f"{emoji} {item.title}")

    if briefing.offer_handle and any(i.actionable for i in briefing.items):
        lines.extend(
            [
                "",
                "Want me to handle any of these?",
                "Reply e.g. *Handle the invoices* or *Handle the incidents*.",
            ]
        )
    else:
        lines.extend(["", "Reply *where are we at?* anytime for a fresh snapshot."])

    return "\n".join(lines)


def category_from_handle_phrase(text: str) -> AttentionCategory | None:
    from miya.services.intelligence.proactive.types import CATEGORY_HANDLE_ALIASES
    import re

    t = (text or "").strip().lower()
    m = re.search(
        r"\b(?:handle|take care of|deal with|process|sort(?:\s+out)?)\s+"
        r"(?:the\s+|my\s+|those\s+|these\s+)?([a-z]+)\b",
        t,
    )
    if not m:
        # bare "invoices" after briefing offer
        m = re.search(r"\b(invoices?|incidents?|tasks?|approvals?|payments?|checklists?|insurance)\b", t)
        if not m:
            return None
        key = m.group(1)
    else:
        key = m.group(1)
    # normalize plurals lightly
    return CATEGORY_HANDLE_ALIASES.get(key) or CATEGORY_HANDLE_ALIASES.get(key.rstrip("s"))
