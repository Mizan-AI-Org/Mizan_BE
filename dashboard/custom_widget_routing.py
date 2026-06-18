"""
Route Miya-created dashboard tasks into user-scoped custom widget tiles.

When a manager creates a tile like "Event Kasbah Dif" with keyword "Kasbah"
and later asks Miya to "print the menus for the kasbah dif event", the task
should land on that tile — not in the global Tasks & Demands feed or a
generic Operations lane.
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

_KEYWORD_INLINE_PATTERNS = (
    re.compile(
        r"(?:keyword|keywords|mot[\s-]?cl[eé]|mots[\s-]?cl[eé]s?)\s*[:\-]?\s*"
        r'["\']?([^"\'\n,.]+)',
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:with|avec)\s+(?:the\s+)?keyword[s]?\s+[\"']?([^\"'\n,.]+)",
        re.IGNORECASE,
    ),
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


def parse_routing_keywords_from_text(*texts: str | None) -> list[str]:
    """Extract explicit keywords from manager phrasing (e.g. keyword Kasbah)."""
    found: list[str] = []
    for text in texts:
        if not text:
            continue
        for pattern in _KEYWORD_INLINE_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            chunk = match.group(1).strip()
            for part in re.split(r"[,;/|]+", chunk):
                part = part.strip()
                if part:
                    found.append(part)
    return found


def normalize_routing_keywords(
    raw: list[str] | str | None,
    *,
    title: str = "",
    subtitle: str = "",
    source_text: str = "",
) -> list[str]:
    """Merge explicit keywords, parsed phrases, and distinctive title tokens."""
    collected: list[str] = []

    if isinstance(raw, str):
        raw = [raw]
    if raw:
        for item in raw:
            for part in re.split(r"[,;/|]+", str(item)):
                part = part.strip()
                if part:
                    collected.append(part)

    for parsed in parse_routing_keywords_from_text(source_text, title, subtitle):
        collected.append(parsed)

    if not collected:
        collected = sorted(
            _significant_tokens(title, subtitle),
            key=len,
            reverse=True,
        )[:5]

    seen: set[str] = set()
    deduped: list[str] = []
    for keyword in collected:
        normalized = _normalize_text(keyword)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(keyword.strip())
    return deduped[:20]


def _widget_keywords(widget: DashboardCustomWidget) -> list[str]:
    raw = getattr(widget, "routing_keywords", None) or []
    if raw:
        return normalize_routing_keywords(raw, title=widget.title, subtitle=widget.subtitle)
    return normalize_routing_keywords(
        None,
        title=widget.title,
        subtitle=widget.subtitle,
    )


def _score_keyword_match(
    widget: DashboardCustomWidget,
    haystack: str,
) -> tuple[int, int]:
    """Return (matched_keyword_count, longest_matched_keyword_len)."""
    matched = 0
    longest = 0
    for keyword in _widget_keywords(widget):
        normalized = _normalize_text(keyword)
        if len(normalized) < 3:
            continue
        if normalized in haystack:
            matched += 1
            longest = max(longest, len(normalized))
    return matched, longest


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
    2. ``routing_keywords`` on the widget (e.g. keyword "Kasbah").
    3. Text overlap between task title/description/source_text and the
       widget title/subtitle.
    """
    if restaurant is None:
        return None

    qs = DashboardCustomWidget.objects.filter(restaurant=restaurant)

    if explicit_id:
        wid = str(explicit_id).strip()
        if wid.lower().startswith("custom:"):
            wid = wid.split(":", 1)[1]
        return qs.filter(id=wid).first()

    haystack = _normalize_text(" ".join([title, description, source_text]))
    if not haystack:
        return None

    best: DashboardCustomWidget | None = None
    best_key: tuple[int, int, int, float, int] = (0, 0, 0, 0.0, 0)

    for widget in qs.only(
        "id",
        "title",
        "subtitle",
        "routing_keywords",
        "user_id",
    ):
        keyword_hits, longest_keyword = _score_keyword_match(widget, haystack)
        title_matched, title_ratio = _score_widget_match(widget, haystack)

        if keyword_hits == 0 and title_matched == 0:
            continue

        if keyword_hits == 0:
            long_hit = any(
                len(tok) >= 6 and tok in haystack
                for tok in _significant_tokens(widget.title, widget.subtitle)
            )
            if title_matched < 2 and not long_hit:
                continue

        owner_bonus = 1 if user is not None and widget.user_id == getattr(user, "id", None) else 0
        key = (
            keyword_hits,
            longest_keyword,
            owner_bonus,
            title_ratio,
            len(_normalize_text(widget.title)),
        )
        if key > best_key:
            best_key = key
            best = widget

    return best


def custom_widget_hint(widget: DashboardCustomWidget | None) -> str:
    if widget is None:
        return ""
    title = (widget.title or "your custom widget").strip()
    return f" Refresh the dashboard — it appears on your {title} widget."


def routing_keywords_from_payload(data: dict[str, Any] | None) -> list[str] | None:
    if not data:
        return None
    raw = data.get("routing_keywords") or data.get("routingKeywords")
    if raw is None:
        raw = data.get("keywords") or data.get("keyword")
    if raw is None:
        return None
    if isinstance(raw, str):
        return normalize_routing_keywords(raw)
    if isinstance(raw, list):
        return normalize_routing_keywords(raw)
    return None
