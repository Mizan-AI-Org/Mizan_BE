"""Phase 10 — Operational Copilot types and mandatory pipeline stages."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CopilotStage(str, Enum):
    """Mandatory action pipeline — every mutation traverses these."""

    UNDERSTAND = "UNDERSTAND"
    CONTEXT = "CONTEXT"
    AUTHORIZE = "AUTHORIZE"
    EXECUTE = "EXECUTE"
    VERIFY = "VERIFY"
    RECORD = "RECORD"
    NOTIFY = "NOTIFY"
    RESPOND = "RESPOND"

    # Extended capabilities (read / reason paths)
    RETRIEVE = "RETRIEVE"
    REMEMBER = "REMEMBER"
    REASON = "REASON"
    SEARCH = "SEARCH"
    PLAN = "PLAN"
    SUMMARIZE = "SUMMARIZE"


MANDATORY_MUTATION_STAGES: tuple[CopilotStage, ...] = (
    CopilotStage.UNDERSTAND,
    CopilotStage.CONTEXT,
    CopilotStage.AUTHORIZE,
    CopilotStage.EXECUTE,
    CopilotStage.VERIFY,
    CopilotStage.RECORD,
    CopilotStage.NOTIFY,
    CopilotStage.RESPOND,
)


@dataclass
class CopilotResult:
    """Outcome from the operational copilot — chat-shaped."""

    reply: str
    success: bool = True
    verified: bool = False
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    stages_completed: list[str] = field(default_factory=list)
    handler: str = ""
    presentation_only: bool = True
    needs_clarification: bool = False
    needs_confirmation: bool = False
    needs_establishment: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    def to_chat_extra(self) -> dict[str, Any]:
        extra = dict(self.meta)
        extra["copilot"] = True
        extra["copilot_handler"] = self.handler
        extra["stages_completed"] = list(self.stages_completed)
        extra["presentation_only"] = self.presentation_only
        if self.needs_clarification:
            extra["needs_clarification"] = True
        if self.needs_confirmation:
            extra["needs_confirmation"] = True
        if self.needs_establishment:
            extra["needs_establishment"] = True
        if self.verified:
            extra["verified"] = True
        return extra
