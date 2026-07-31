"""Cache key helpers for Miya / Mastra integration."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def whatsapp_context_key(phone_digits: str) -> str:
    return f"miya:wa-ctx:{phone_digits}"


def mastra_tool_key(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    user_id: str | None,
    restaurant_id: str | None,
    channel: str,
) -> str:
    payload = {
        "tool": tool_name,
        "args": arguments or {},
        "uid": str(user_id or ""),
        "rid": str(restaurant_id or ""),
        "ch": (channel or "dashboard").strip().lower(),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:32]
    return f"miya:mastra:tool:{digest}"


def mastra_health_key() -> str:
    return "miya:mastra:health"
