"""Compound plan execution — sequential canonical steps with per-step results (Phase 12.5)."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from miya.services.intelligence.actions import execute_structured_action
from miya.services.intelligence.copilot.authorize import authorize_mutation
from miya.services.intelligence.planning.compound import CompoundPlan, PlanStep
from miya.services.intelligence.planning.types import ClassifiedIntent, IntentClass
from miya.services.ops.context import OpsContext
from miya.services.ops.result import OpsResult

logger = logging.getLogger("miya.intelligence.compound")


@dataclass
class StepResult:
    order: int
    action: str
    description: str
    success: bool
    verified: bool = False
    skipped: bool = False
    error: str = ""
    result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "order": self.order,
            "action": self.action,
            "description": self.description,
            "success": self.success,
            "verified": self.verified,
            "skipped": self.skipped,
            "error": self.error,
            "result": self.result,
        }


@dataclass
class CompoundExecutionOutcome:
    success: bool
    verified: bool
    step_results: list[StepResult]
    reply: str
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    primary_task_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "verified": self.verified,
            "step_results": [s.to_dict() for s in self.step_results],
            "primary_task_id": self.primary_task_id,
        }


def compound_plan_from_dict(data: dict[str, Any] | None) -> CompoundPlan | None:
    if not data or not data.get("compound"):
        return None
    steps = []
    for row in data.get("steps") or []:
        steps.append(
            PlanStep(
                order=int(row.get("order") or 0),
                intent=IntentClass(row.get("intent") or "QUERY"),
                action=str(row.get("action") or ""),
                description=str(row.get("description") or ""),
                tool_args=dict(row.get("tool_args") or {}),
                requires_verify=bool(row.get("requires_verify", True)),
                notify=bool(row.get("notify")),
            )
        )
    if len(steps) < 2:
        return None
    return CompoundPlan(steps=steps, raw_message=str(data.get("raw_message") or ""))


def execute_compound_plan(
    plan: CompoundPlan,
    *,
    classified: ClassifiedIntent,
    ctx: OpsContext,
    execution_context: dict[str, Any] | None = None,
) -> CompoundExecutionOutcome:
    """
    Execute compound plan steps sequentially.

    - Step failure stops dependent steps (notify after failed complete).
    - Successful mutation with failed notify → partial success message.
    - Each step exposes individual result for inspection.
    """
    exec_ctx = dict(execution_context or {})
    step_results: list[StepResult] = []
    tool_trace: list[dict[str, Any]] = []
    primary_task_id = ""
    mutation_verified = False

    for step in sorted(plan.steps, key=lambda s: s.order):
        if step.action in ("complete_task", "assign_ops_task"):
            denied = authorize_mutation(classified, ctx)
            if denied:
                sr = StepResult(
                    order=step.order,
                    action=step.action,
                    description=step.description,
                    success=False,
                    error=denied.message_for_user or "Permission denied.",
                )
                step_results.append(sr)
                return CompoundExecutionOutcome(
                    success=False,
                    verified=False,
                    step_results=step_results,
                    reply=sr.error,
                    tool_trace=tool_trace,
                )

        args = dict(step.tool_args or {})
        if primary_task_id and step.action in ("complete_task", "notify_manager_urgent"):
            args.setdefault("task_id", primary_task_id)

        oid = str(uuid.uuid4())
        args["_operation_id"] = oid
        exec_ctx["operation_id"] = oid

        try:
            ops_result = execute_structured_action(
                step.action,
                args,
                ctx=ctx,
                execution_context=exec_ctx,
                intent=step.intent.value,
            )
        except Exception as exc:
            logger.exception("compound step %s failed", step.action)
            sr = StepResult(
                order=step.order,
                action=step.action,
                description=step.description,
                success=False,
                error=str(exc),
            )
            step_results.append(sr)
            if step.action in ("complete_task", "assign_ops_task"):
                return _build_outcome(
                    step_results=step_results,
                    tool_trace=tool_trace,
                    primary_task_id=primary_task_id,
                    mutation_verified=False,
                )
            continue

        payload = ops_result.as_tool_response() if hasattr(ops_result, "as_tool_response") else {}
        tool_trace.append(
            {
                "tool": step.action,
                "arguments": args,
                "result": payload,
                "verified": bool(getattr(ops_result, "verified", False)),
            }
        )

        tid = _extract_task_id(ops_result)
        if tid:
            primary_task_id = tid

        sr = StepResult(
            order=step.order,
            action=step.action,
            description=step.description,
            success=bool(ops_result.success),
            verified=bool(getattr(ops_result, "verified", False)),
            error="" if ops_result.success else (ops_result.message_for_user or ops_result.code or "failed"),
            result=payload,
        )
        step_results.append(sr)

        if step.action in ("complete_task", "assign_ops_task"):
            mutation_verified = sr.verified and sr.success
            if not sr.success:
                return _build_outcome(
                    step_results=step_results,
                    tool_trace=tool_trace,
                    primary_task_id=primary_task_id,
                    mutation_verified=False,
                )

        if step.notify and not sr.success:
            # Notify failure after successful mutation — do not roll back
            return _build_outcome(
                step_results=step_results,
                tool_trace=tool_trace,
                primary_task_id=primary_task_id,
                mutation_verified=mutation_verified,
                notify_failed=True,
            )

    return _build_outcome(
        step_results=step_results,
        tool_trace=tool_trace,
        primary_task_id=primary_task_id,
        mutation_verified=mutation_verified,
    )


def _extract_task_id(result: OpsResult) -> str:
    data = getattr(result, "data", None) or {}
    task = data.get("task") or {}
    return str(task.get("id") or data.get("task_id") or "")


def _build_outcome(
    *,
    step_results: list[StepResult],
    tool_trace: list[dict[str, Any]],
    primary_task_id: str,
    mutation_verified: bool,
    notify_failed: bool = False,
) -> CompoundExecutionOutcome:
    all_ok = all(s.success or s.skipped for s in step_results)
    verified = mutation_verified

    if notify_failed and mutation_verified:
        reply = "Task completed, but I couldn't notify the manager."
        return CompoundExecutionOutcome(
            success=True,
            verified=mutation_verified,
            step_results=step_results,
            reply=reply,
            tool_trace=tool_trace,
            primary_task_id=primary_task_id,
        )

    if not all_ok:
        last_err = next((s.error for s in reversed(step_results) if s.error), "")
        reply = f"I couldn't complete that because {last_err or 'the operation failed'}."
        return CompoundExecutionOutcome(
            success=False,
            verified=False,
            step_results=step_results,
            reply=reply,
            tool_trace=tool_trace,
            primary_task_id=primary_task_id,
        )

    reply = "Done."
    if mutation_verified:
        reply = "Done — task completed."
    return CompoundExecutionOutcome(
        success=True,
        verified=mutation_verified,
        step_results=step_results,
        reply=reply,
        tool_trace=tool_trace,
        primary_task_id=primary_task_id,
    )
