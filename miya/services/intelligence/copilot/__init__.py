"""
Phase 10 — Miya Operational Copilot.

One coherent experience integrating UNDERSTAND, RETRIEVE, REMEMBER, REASON,
SEARCH, PLAN, EXECUTE, VERIFY, NOTIFY, SUMMARIZE, proactive alerts, multimodal,
WhatsApp, voice, and multi-establishment intelligence.

Every mutation: UNDERSTAND → CONTEXT → AUTHORIZE → EXECUTE → VERIFY → RECORD → NOTIFY → RESPOND.
"""
from __future__ import annotations

from miya.services.intelligence.copilot.orchestrator import run_copilot_turn
from miya.services.intelligence.copilot.types import (
    MANDATORY_MUTATION_STAGES,
    CopilotResult,
    CopilotStage,
)
from miya.services.intelligence.copilot.understand import (
    is_mutation_intent,
    is_operational_search_query,
    understand_turn,
)

__all__ = [
    "MANDATORY_MUTATION_STAGES",
    "CopilotResult",
    "CopilotStage",
    "is_mutation_intent",
    "is_operational_search_query",
    "run_copilot_turn",
    "understand_turn",
]
