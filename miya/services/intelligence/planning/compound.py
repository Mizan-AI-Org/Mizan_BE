"""Compound intent detection — explicit multi-step plans (Phase 12)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from miya.services.intelligence.planning.types import ClassifiedIntent, IntentClass


@dataclass
class PlanStep:
    order: int
    intent: IntentClass
    action: str  # canonical handler key
    description: str
    tool_args: dict[str, Any] = field(default_factory=dict)
    requires_verify: bool = True
    notify: bool = False


@dataclass
class CompoundPlan:
    steps: list[PlanStep]
    raw_message: str
    compound: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "compound": self.compound,
            "steps": [
                {
                    "order": s.order,
                    "intent": s.intent.value,
                    "action": s.action,
                    "description": s.description,
                    "requires_verify": s.requires_verify,
                    "notify": s.notify,
                }
                for s in self.steps
            ],
        }


_COMPOUND_SPLIT = re.compile(
    r"\s+(?:and\s+then|then|and)\s+(?=tell|notify|let|inform|send|complete|mark|assign|approve)",
    re.I,
)


def detect_compound_intent(message: str, classified: ClassifiedIntent) -> CompoundPlan | None:
    """
    Detect compound requests and return an inspectable structured plan.

    Example: "Complete Ahmed's closing task and tell the manager."
    """
    text = (message or "").strip()
    if not text or not _COMPOUND_SPLIT.search(text):
        return None

    parts = _COMPOUND_SPLIT.split(text, maxsplit=1)
    if len(parts) < 2:
        return None

    primary_text = parts[0].strip()
    secondary_text = parts[1].strip()

    steps: list[PlanStep] = []

    # Primary mutation from classified intent
    if classified.intent == IntentClass.COMPLETE:
        steps.append(
            PlanStep(
                order=1,
                intent=IntentClass.COMPLETE,
                action="complete_task",
                description="Complete the task",
                tool_args={"q": classified.query or primary_text},
                requires_verify=True,
            )
        )
    elif classified.intent == IntentClass.ASSIGN:
        steps.append(
            PlanStep(
                order=1,
                intent=IntentClass.ASSIGN,
                action="assign_ops_task",
                description="Assign the task",
                tool_args={
                    "q": classified.query or "",
                    "assignee_name": classified.assignee_hint or "",
                },
                requires_verify=True,
            )
        )
    else:
        return None

    # Secondary notify step
    if re.search(r"\b(tell|notify|let|inform)\s+(?:the\s+)?manager\b", secondary_text, re.I):
        steps.append(
            PlanStep(
                order=2,
                intent=IntentClass.QUERY,
                action="notify_manager_urgent",
                description="Notify manager",
                requires_verify=True,
                notify=True,
            )
        )
    elif re.search(r"\b(tell|notify|let|inform)\b", secondary_text, re.I):
        steps.append(
            PlanStep(
                order=2,
                intent=IntentClass.QUERY,
                action="notify_manager_urgent",
                description="Notify responsible manager",
                requires_verify=True,
                notify=True,
            )
        )
    else:
        return None

    if len(steps) < 2:
        return None

    return CompoundPlan(steps=steps, raw_message=text)
