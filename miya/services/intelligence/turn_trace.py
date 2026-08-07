"""Phase 12 turn-level tracing — observability without logging sensitive content."""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger("miya.turn_trace")


@dataclass
class TurnTrace:
    request_id: str
    message_id: str = ""
    conversation_id: str = ""
    user_id: str = ""
    tenant_id: str = ""
    establishment_id: str = ""
    channel: str = "dashboard"
    intent: str = ""
    entity_type: str = ""
    entity_id: str = ""
    routing_hint: str = ""
    plan_workflow: str = ""
    plan_action: str = ""
    compound: bool = False
    tools_selected: list[str] = field(default_factory=list)
    handler: str = ""
    stages_completed: list[str] = field(default_factory=list)
    verified: bool | None = None
    audit_event_id: str = ""
    notification_sent: bool | None = None
    outcome: str = ""  # success | clarify | denied | failed | defer
    elapsed_ms: float = 0.0
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    error: str = ""

    def to_log_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Never log raw message body
        return d


def new_turn_trace(
    *,
    message_id: str = "",
    conversation_id: str = "",
    user_id: str = "",
    tenant_id: str = "",
    establishment_id: str = "",
    channel: str = "dashboard",
) -> TurnTrace:
    return TurnTrace(
        request_id=str(uuid.uuid4()),
        message_id=message_id,
        conversation_id=conversation_id,
        user_id=user_id,
        tenant_id=tenant_id,
        establishment_id=establishment_id,
        channel=channel,
    )


def record_turn_trace(trace: TurnTrace) -> None:
    """Emit structured turn trace (log-only; no sensitive message content)."""
    try:
        logger.info("MIYA_TURN_TRACE %s", trace.to_log_dict())
    except Exception:
        pass


class TurnTraceTimer:
    def __init__(self, trace: TurnTrace):
        self.trace = trace
        self._start = time.perf_counter()

    def finish(self, *, outcome: str = "success") -> TurnTrace:
        self.trace.elapsed_ms = (time.perf_counter() - self._start) * 1000
        self.trace.outcome = outcome
        record_turn_trace(self.trace)
        return self.trace
