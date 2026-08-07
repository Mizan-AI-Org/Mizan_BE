"""
Phase 6 — Proactive Operational Intelligence.

Daily Operations Intelligence: scan reality → structured briefing →
optional handle workflows. Prefs, severity, and dedupe prevent spam.
"""
from __future__ import annotations

from miya.services.intelligence.proactive.briefing import (
    category_from_handle_phrase,
    format_daily_briefing,
)
from miya.services.intelligence.proactive.delivery import (
    build_and_maybe_deliver,
    on_demand_briefing,
)
from miya.services.intelligence.proactive.handle import try_handle_briefing_request
from miya.services.intelligence.proactive.scanner import scan_daily_operations

__all__ = [
    "build_and_maybe_deliver",
    "category_from_handle_phrase",
    "format_daily_briefing",
    "on_demand_briefing",
    "scan_daily_operations",
    "try_handle_briefing_request",
]
