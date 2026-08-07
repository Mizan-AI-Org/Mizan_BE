"""Deduplication + escalation for proactive briefings — no spam."""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from django.core.cache import cache
from django.utils import timezone

from miya.services.intelligence.proactive.types import DailyBriefing, Severity

logger = logging.getLogger("miya.intelligence.proactive.dedupe")

TTL_DAY = 86400
TTL_CTX = 36 * 3600  # keep handle context into next morning


def compute_fingerprint(briefing: DailyBriefing) -> str:
    parts: list[str] = []
    for item in sorted(briefing.items, key=lambda i: (i.category.value, i.severity.value)):
        parts.extend(item.fingerprint_parts())
    raw = "|".join(parts) or "empty"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _day_key(restaurant_id: str, user_id: str, period: str, day: str | None = None) -> str:
    d = day or timezone.localdate().isoformat()
    return f"daily_ops_intel:{period}:{restaurant_id}:{user_id}:{d}"


def _fp_key(restaurant_id: str, user_id: str) -> str:
    return f"daily_ops_intel_fp:{restaurant_id}:{user_id}"


def _ctx_key(restaurant_id: str, user_id: str) -> str:
    return f"daily_ops_briefing_ctx:{restaurant_id}:{user_id}"


def should_send_briefing(
    briefing: DailyBriefing,
    *,
    user_id: str,
    force: bool = False,
) -> tuple[bool, str]:
    """
    Send when:
      - force=True (explicit on-demand), or
      - not yet sent today for this period, or
      - fingerprint changed AND severity escalated vs last send
    Never re-send identical state the same day.
    """
    if not briefing.items and not force:
        return False, "nothing_needs_attention"

    fp = briefing.fingerprint or compute_fingerprint(briefing)
    briefing.fingerprint = fp
    day_key = _day_key(briefing.restaurant_id, user_id, briefing.period)
    prev_fp = cache.get(_fp_key(briefing.restaurant_id, user_id))

    if force:
        return True, "forced"

    if cache.get(day_key):
        # Already sent today — only escalate if fingerprint changed to higher severity
        if prev_fp and prev_fp == fp:
            return False, "duplicate_same_state"
        if _escalated(briefing, prev_fp):
            return True, "escalation"
        return False, "already_sent_today_no_escalation"

    return True, "morning_or_first"


def mark_sent(briefing: DailyBriefing, *, user_id: str) -> None:
    fp = briefing.fingerprint or compute_fingerprint(briefing)
    briefing.fingerprint = fp
    cache.set(
        _day_key(briefing.restaurant_id, user_id, briefing.period),
        fp,
        TTL_DAY,
    )
    cache.set(_fp_key(briefing.restaurant_id, user_id), fp, TTL_DAY)
    store_briefing_context(briefing, user_id=user_id)


def store_briefing_context(briefing: DailyBriefing, *, user_id: str) -> None:
    cache.set(_ctx_key(briefing.restaurant_id, user_id), briefing.to_dict(), TTL_CTX)


def load_briefing_context(restaurant_id: str, user_id: str) -> DailyBriefing | None:
    row = cache.get(_ctx_key(restaurant_id, user_id))
    if not isinstance(row, dict):
        return None
    try:
        return DailyBriefing.from_dict(row)
    except Exception:
        logger.exception("load_briefing_context failed")
        return None


def _escalated(briefing: DailyBriefing, prev_fp: Any) -> bool:
    """True if any CRITICAL/HIGH item appears (state worsened)."""
    if not prev_fp:
        return True
    return any(
        i.severity in (Severity.CRITICAL, Severity.HIGH) for i in briefing.items
    )


def stable_json(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, default=str)
