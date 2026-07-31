"""Sanitize internal API errors before Miya relays them to end users."""

from __future__ import annotations

import re
from typing import Any

_INTERNAL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"restaurant_id|restaurantId|sessionId|userId", re.I),
        "I couldn't determine your workspace from this session. Make sure you're logged in or your WhatsApp number is linked to your staff account.",
    ),
    (
        re.compile(r"valid UUIDs?", re.I),
        "I couldn't match that person or record. Try the name again or use the task reference from the notification.",
    ),
    (
        re.compile(r"Unable to resolve restaurant context", re.I),
        "I couldn't determine your workspace from this session. Make sure you're logged in or your WhatsApp number is linked to your staff account.",
    ),
    (
        re.compile(r"Unable to resolve workspace", re.I),
        "I couldn't determine your workspace from this session.",
    ),
)


def sanitize_user_error(message: Any) -> str:
    """Strip internal identifiers from error strings shown to users."""
    text = str(message or "").strip()
    if not text:
        return "Something went wrong. Please try again in a moment."
    for pattern, replacement in _INTERNAL_PATTERNS:
        if pattern.search(text):
            return replacement
    # Strip raw UUID-looking fragments from generic errors
    if re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", text, re.I):
        return "Something went wrong on our side. Please try again or rephrase your request."
    return text[:400]


def pick_user_message(body: dict[str, Any] | None, *, fallback: str = "") -> str:
    """Prefer message_for_user over raw error fields."""
    if not isinstance(body, dict):
        return sanitize_user_error(fallback or "Something went wrong.")
    for key in ("message_for_user", "message", "detail"):
        val = body.get(key)
        if isinstance(val, str) and val.strip():
            return sanitize_user_error(val)
    err = body.get("error")
    if isinstance(err, str) and err.strip():
        return sanitize_user_error(err)
    return sanitize_user_error(fallback or "Something went wrong.")
