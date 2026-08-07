"""Phase 3 planning types — intent, entity, confidence, plan."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class IntentClass(str, Enum):
    QUERY = "QUERY"
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    ASSIGN = "ASSIGN"
    COMPLETE = "COMPLETE"
    DELETE = "DELETE"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    ROUTE = "ROUTE"
    UPLOAD = "UPLOAD"
    RETRIEVE = "RETRIEVE"
    REMIND = "REMIND"
    SCHEDULE = "SCHEDULE"
    ANALYZE = "ANALYZE"
    SUMMARIZE = "SUMMARIZE"
    UNKNOWN = "UNKNOWN"


class EntityType(str, Enum):
    TASK = "task"
    INCIDENT = "incident"
    STAFF = "staff"
    CATEGORY = "category"
    ESTABLISHMENT = "establishment"
    DOCUMENT = "document"
    INVOICE = "invoice"
    MEETING = "meeting"
    REMINDER = "reminder"
    APPROVAL = "approval"
    UNKNOWN = "unknown"


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class PlanAction(str, Enum):
    EXECUTE = "EXECUTE"
    CONFIRM = "CONFIRM"
    CLARIFY = "CLARIFY"
    DEFER_TO_AGENT = "DEFER_TO_AGENT"


PIPELINE_STAGES = (
    "UNDERSTAND",
    "IDENTIFY",
    "RETRIEVE",
    "REASON",
    "PLAN",
    "EXECUTE",
    "VERIFY",
    "RESPOND",
)


@dataclass
class ClassifiedIntent:
    intent: IntentClass
    entity_type: EntityType
    confidence: Confidence
    query: str = ""
    assignee_hint: str = ""
    status_hint: str = ""
    pronoun: bool = False
    raw_message: str = ""
    slots: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "entity_type": self.entity_type.value,
            "confidence": self.confidence.value,
            "query": self.query,
            "assignee_hint": self.assignee_hint,
            "status_hint": self.status_hint,
            "pronoun": self.pronoun,
            "slots": dict(self.slots),
            "reasons": list(self.reasons),
        }


@dataclass
class ExecutionPlan:
    workflow: str
    action: PlanAction
    intent: ClassifiedIntent
    steps: list[str] = field(default_factory=list)
    entity_id: str = ""
    entity_ids: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    clarification_message: str = ""
    confirm_message: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    stage: str = "PLAN"

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow": self.workflow,
            "action": self.action.value,
            "intent": self.intent.to_dict(),
            "steps": list(self.steps),
            "entity_id": self.entity_id or None,
            "entity_ids": list(self.entity_ids),
            "candidates": list(self.candidates),
            "clarification_message": self.clarification_message or None,
            "confirm_message": self.confirm_message or None,
            "tool_args": dict(self.tool_args),
            "stage": self.stage,
            "pipeline": list(PIPELINE_STAGES),
        }


@dataclass
class PlanResult:
    """Presentation-only final outcome — never re-enter as a command."""

    reply: str
    success: bool
    verified: bool = False
    needs_clarification: bool = False
    needs_confirmation: bool = False
    plan: ExecutionPlan | None = None
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    stages_completed: list[str] = field(default_factory=list)
    presentation_only: bool = True

    def as_chat_result(self) -> dict[str, Any]:
        return {
            "reply": self.reply,
            "tool_trace": list(self.tool_trace),
            "planning": self.plan.to_dict() if self.plan else None,
            "stages_completed": list(self.stages_completed),
            "needs_clarification": self.needs_clarification,
            "needs_confirmation": self.needs_confirmation,
            "verified": self.verified,
            "success": self.success,
            "assistant_text_is_not_executable": True,
            "presentation_only": True,
            "planning_engine": True,
        }
