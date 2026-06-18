"""
Route Miya-created dashboard tasks into user-scoped custom widget tiles.

When a manager creates a tile like "Event Kasbah Dif" and later asks Miya
to "print the menus for the kasbah dif event", the task should land on
that tile — not in the global Tasks & Demands feed or a generic Operations
lane.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from dashboard.models import DashboardCustomWidget

# Generic words that appear in many widget titles but aren't useful for
# text matching against task titles.
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "for",
        "to",
        "of",
        "in",
        "on",
        "at",
        "my",
        "our",
        "your",
        "event",
        "events",
        "widget",
        "tile",
        "board",
        "dashboard",
        "team",
        "staff",
        "mizan",
        "miya",
        "lane",
        "quick",
        "link",
        "shortcut",
    }
)


def _normalize_text(text: str) -> str:
    s = (text or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _significant_tokens(*parts: str | None) -> set[str]:
    out: set[str] = set()
    for part in parts:
        if not part:
            continue
        for word in _normalize_text(part).split():
            if len(word) >= 3 and word not in _STOP_WORDS:
                out.add(word)
    return out


def _score_widget_match(widget: DashboardCustomWidget, haystack: str) -> tuple[int, float]:
    """Return (matched_token_count, match_ratio). Higher is better."""
    tokens = _significant_tokens(widget.title, widget.subtitle)
    if not tokens:
        return 0, 0.0
    matched = sum(1 for tok in tokens if tok in haystack)
    if matched == 0:
        return 0, 0.0
    return matched, matched / len(tokens)


def match_custom_widget_for_task(
    *,
    user,
    restaurant,
    title: str,
    description: str = "",
    source_text: str = "",
    explicit_id: str | None = None,
) -> DashboardCustomWidget | None:
    """Pick the best custom widget tile for a newly-created task.

    Priority:
    1. Explicit ``custom_widget_id`` from the agent payload.
    2. Text overlap between task title/description/source_text and the
       manager's custom widget titles (e.g. "Event Kasbah Dif" ↔
       "print menus for the kasbah dif event").
    """
    if user is None or restaurant is None:
        return None

    qs = DashboardCustomWidget.objects.filter(user=user, restaurant=restaurant)

    if explicit_id:
        wid = str(explicit_id).strip()
        if wid.lower().startswith("custom:"):
            wid = wid.split(":", 1)[1]
        return qs.filter(id=wid).first()

    haystack = _normalize_text(" ".join([title, description, source_text]))
    if not haystack:
        return None

    best: DashboardCustomWidget | None = None
    best_key: tuple[int, float, int] = (0, 0.0, 0)

    for widget in qs.only("id", "title", "subtitle"):
        matched, ratio = _score_widget_match(widget, haystack)
        if matched == 0:
            continue
        # Require meaningful overlap: two+ tokens, or one long distinctive token.
        long_hit = any(
            len(tok) >= 6 and tok in haystack
            for tok in _significant_tokens(widget.title, widget.subtitle)
        )
        if matched < 2 and not long_hit:
            continue
        key = (matched, ratio, len(_normalize_text(widget.title)))
        if key > best_key:
            best_key = key
            best = widget

    return best


def custom_widget_hint(widget: DashboardCustomWidget | None) -> str:
    if widget is None:
        return ""
    title = (widget.title or "your custom widget").strip()
    return f" Refresh the dashboard — it appears on your {title} widget."
