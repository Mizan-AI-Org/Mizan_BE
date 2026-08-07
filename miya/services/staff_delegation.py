"""Parse manager→single-staff delegation ("tell Adama to …") for Miya routing."""

from __future__ import annotations

import re
from typing import Any

_BROADCAST_PHRASES = (
    "tell the team",
    "tell everyone",
    "tell all staff",
    "tell all employees",
    "tell the staff",
    "announce to",
    "message everyone",
    "message the team",
    "notify everyone",
    "notify all staff",
    "let everyone know",
    "let the team know",
    "inform the team",
    "inform everyone",
)

_CATEGORY_DELEGATION = re.compile(
    r"\b(tell|ask|notify|remind|message|ping|have)\s+"
    r"(hr|human resources|payroll|finance|maintenance|kitchen|housekeeping)\b",
    re.I,
)

_SINGLE_DELEGATION = re.compile(
    r"^(?:please\s+|can you\s+|could you\s+|miya\s+|hey miya\s+|hi miya\s+)?"
    r"(?:tell|ask|notify|remind|message|ping|let)\s+"
    r"(?!(?:the|all|every)\s+(?:team|staff|everyone|employees)\b)"
    r"(?!everyone\b)"
    r"([a-zA-Z\u00C0-\u024F\u0600-\u06FF'’.-]+(?:\s+[a-zA-Z\u00C0-\u024F\u0600-\u06FF'’.-]+)?)"
    r"\s+(?:to\s+|that\s+|about\s+)?(.+)$",
    re.I,
)


def looks_like_broadcast(message: str) -> bool:
    lower = (message or "").strip().lower()
    if not lower:
        return False
    return any(p in lower for p in _BROADCAST_PHRASES)


def looks_like_category_delegation(message: str) -> bool:
    return bool(_CATEGORY_DELEGATION.search(message or ""))


def parse_staff_delegation(message: str) -> dict[str, str] | None:
    """
    Extract a single staff target + task from messages like
    "tell Adama to prepare the buffet".
    """
    text = (message or "").strip()
    if not text or looks_like_broadcast(text) or looks_like_category_delegation(text):
        return None

    match = _SINGLE_DELEGATION.match(text)
    if not match:
        return None

    staff_name = (match.group(1) or "").strip()
    task_body = (match.group(2) or "").strip()
    if not staff_name or not task_body:
        return None

    # Drop polite trailing punctuation from the task phrase.
    task_body = task_body.rstrip(" .!?")
    if not task_body:
        return None

    # Title: action-oriented, capped for dashboard cards.
    task_title = task_body[:1].upper() + task_body[1:] if task_body else task_body
    if len(task_title) > 120:
        task_title = task_title[:117].rstrip() + "…"

    return {
        "staff_name": staff_name,
        "task_title": task_title,
        "task_description": text,
    }


def audience_is_broadcast(audience: Any) -> bool:
    if audience is None:
        return False
    if isinstance(audience, str):
        return audience.strip().lower() in {"all", "everyone", "all_staff", "team"}
    if isinstance(audience, dict):
        if audience.get("all") is True:
            return True
        if str(audience.get("scope") or "").lower() in {"all", "everyone", "team"}:
            return True
        # Specific filters → not a broadcast.
        for key in ("staff_ids", "roles", "departments", "tags"):
            val = audience.get(key)
            if val:
                return False
        return False
    return False


def audience_has_specific_targets(audience: Any) -> bool:
    if not isinstance(audience, dict):
        return False
    for key in ("staff_ids", "roles", "departments", "tags"):
        val = audience.get(key)
        if val:
            return True
    return False
