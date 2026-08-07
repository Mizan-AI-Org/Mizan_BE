"""Deterministic workflow runners — agents reason; workflows execute."""
from __future__ import annotations

from typing import Any, Callable

from miya.services.intelligence.actions import execute_structured_action
from miya.services.intelligence.planning.types import ExecutionPlan, PlanResult
from miya.services.ops.context import OpsContext
from miya.services.ops.result import OpsResult


def run_task_completion(
    ctx: OpsContext,
    plan: ExecutionPlan,
    *,
    execution_context: dict[str, Any] | None = None,
) -> PlanResult:
    stages = ["UNDERSTAND", "IDENTIFY", "RETRIEVE", "REASON", "PLAN", "EXECUTE"]
    result = execute_structured_action(
        "complete_task",
        plan.tool_args,
        ctx=ctx,
        execution_context=execution_context,
        intent="COMPLETE",
    )
    return _to_plan_result(result, plan, stages, success_prefix="Done")


def run_task_assignment(
    ctx: OpsContext,
    plan: ExecutionPlan,
    *,
    execution_context: dict[str, Any] | None = None,
) -> PlanResult:
    stages = ["UNDERSTAND", "IDENTIFY", "RETRIEVE", "REASON", "PLAN", "EXECUTE"]
    if plan.tool_args.get("assign_to_category") and not plan.tool_args.get("task_id"):
        # "Send this to HR" without a prior task → create/delegate via category owners clarify
        if not plan.entity_id:
            return PlanResult(
                reply=(
                    "I can route that to HR — which task or request should I send? "
                    "I won't guess."
                ),
                success=False,
                needs_clarification=True,
                plan=plan,
                stages_completed=stages[:5],
            )
    result = execute_structured_action(
        "assign_task",
        plan.tool_args,
        ctx=ctx,
        execution_context=execution_context,
        intent="ASSIGN",
    )
    return _to_plan_result(result, plan, stages, success_prefix="Assigned")


def run_incident_routing(
    ctx: OpsContext,
    plan: ExecutionPlan,
    *,
    execution_context: dict[str, Any] | None = None,
) -> PlanResult:
    stages = ["UNDERSTAND", "IDENTIFY", "RETRIEVE", "REASON", "PLAN", "EXECUTE"]
    from miya.services.intelligence.planning.types import IntentClass

    if plan.intent.intent == IntentClass.CREATE:
        result = execute_structured_action(
            "create_incident",
            plan.tool_args,
            ctx=ctx,
            execution_context=execution_context,
            intent="CREATE",
        )
        # Auto-route after create when possible
        if result.success and result.verified:
            inc = (result.data or {}).get("incident") or {}
            iid = str(inc.get("id") or "")
            if iid:
                route = execute_structured_action(
                    "assign_incident",
                    {"incident_id": iid},
                    ctx=ctx,
                    execution_context=execution_context,
                    intent="ROUTE",
                )
                trace = [
                    {"tool": "create_incident", "result": result.as_tool_response()},
                    {"tool": "assign_incident", "result": route.as_tool_response()},
                ]
                reply = result.message_for_user
                if route.success:
                    reply = f"{reply} {route.message_for_user}".strip()
                return PlanResult(
                    reply=reply,
                    success=result.success,
                    verified=result.verified,
                    plan=plan,
                    tool_trace=trace,
                    stages_completed=stages + ["VERIFY", "RESPOND"],
                )
        return _to_plan_result(result, plan, stages, success_prefix="Logged")

    result = execute_structured_action(
        "assign_incident",
        plan.tool_args,
        ctx=ctx,
        execution_context=execution_context,
        intent="ROUTE",
    )
    return _to_plan_result(result, plan, stages, success_prefix="Routed")


def run_document_processing(
    ctx: OpsContext,
    plan: ExecutionPlan,
    *,
    execution_context: dict[str, Any] | None = None,
) -> PlanResult:
    stages = ["UNDERSTAND", "IDENTIFY", "RETRIEVE", "REASON", "PLAN", "EXECUTE"]
    result = execute_structured_action(
        "retrieve_document",
        {**plan.tool_args, "show": True},
        ctx=ctx,
        execution_context=execution_context,
        intent="RETRIEVE",
    )
    return _to_plan_result(result, plan, stages, success_prefix="Found")


def run_invoice_approval(
    ctx: OpsContext,
    plan: ExecutionPlan,
    *,
    execution_context: dict[str, Any] | None = None,
) -> PlanResult:
    stages = ["UNDERSTAND", "IDENTIFY", "RETRIEVE", "REASON", "PLAN", "EXECUTE"]
    from miya.services.intelligence.planning.types import IntentClass

    action_name = (
        "approve_invoice"
        if plan.intent.intent == IntentClass.APPROVE
        else "reject_invoice"
        if plan.intent.intent == IntentClass.REJECT
        else "submit_invoice"
    )
    result = execute_structured_action(
        action_name,
        plan.tool_args,
        ctx=ctx,
        execution_context=execution_context,
        intent=plan.intent.intent.value,
    )
    return _to_plan_result(result, plan, stages, success_prefix="Updated")


def run_reminder_creation(
    ctx: OpsContext,
    plan: ExecutionPlan,
    *,
    execution_context: dict[str, Any] | None = None,
) -> PlanResult:
    stages = ["UNDERSTAND", "IDENTIFY", "RETRIEVE", "REASON", "PLAN", "EXECUTE"]
    args = dict(plan.tool_args)
    # Prefer compliance sync when about insurance and no due_at
    if not args.get("due_at") and "insurance" in (args.get("title") or args.get("q") or "").lower():
        from miya.services.ops.meetings import sync_compliance_reminder

        result = sync_compliance_reminder(ctx, q=args.get("title") or args.get("q") or "insurance")
        return _to_plan_result(result, plan, stages, success_prefix="Reminder")
    if not args.get("due_at"):
        return PlanResult(
            reply="When should I remind you? Give a date and time — I won't guess.",
            success=False,
            needs_clarification=True,
            plan=plan,
            stages_completed=stages[:5],
        )
    result = execute_structured_action(
        "create_reminder",
        args,
        ctx=ctx,
        execution_context=execution_context,
        intent="REMIND",
    )
    return _to_plan_result(result, plan, stages, success_prefix="Reminder set")


def run_meeting_creation(
    ctx: OpsContext,
    plan: ExecutionPlan,
    *,
    execution_context: dict[str, Any] | None = None,
) -> PlanResult:
    stages = ["UNDERSTAND", "IDENTIFY", "RETRIEVE", "REASON", "PLAN", "EXECUTE"]
    args = dict(plan.tool_args)
    if not args.get("start") and not args.get("title"):
        return PlanResult(
            reply="What should I schedule, and when? I need a title and start time.",
            success=False,
            needs_clarification=True,
            plan=plan,
            stages_completed=stages[:5],
        )
    result = execute_structured_action(
        "create_meeting",
        args,
        ctx=ctx,
        execution_context=execution_context,
        intent="SCHEDULE",
    )
    return _to_plan_result(result, plan, stages, success_prefix="Scheduled")


WORKFLOWS: dict[str, Callable[..., PlanResult]] = {
    "task_completion": run_task_completion,
    "task_assignment": run_task_assignment,
    "incident_routing": run_incident_routing,
    "document_processing": run_document_processing,
    "invoice_approval": run_invoice_approval,
    "reminder_creation": run_reminder_creation,
    "meeting_creation": run_meeting_creation,
}


def _register_multimodal_workflows() -> None:
    from miya.services.intelligence.planning.multimodal_workflows import (
        run_compliance_reminder_from_media,
        run_incident_from_media,
        run_incident_lookup,
        run_invoice_from_media,
        run_staff_lookup,
    )

    WORKFLOWS.update(
        {
            "incident_from_media": run_incident_from_media,
            "invoice_from_media": run_invoice_from_media,
            "compliance_reminder_from_media": run_compliance_reminder_from_media,
            "incident_lookup": run_incident_lookup,
            "staff_lookup": run_staff_lookup,
        }
    )


_register_multimodal_workflows()


def _to_plan_result(
    result: OpsResult,
    plan: ExecutionPlan,
    stages: list[str],
    *,
    success_prefix: str,
) -> PlanResult:
    body = result.as_tool_response()
    verified = bool(result.verified and result.success)
    stages_done = list(stages)
    if verified:
        stages_done.extend(["VERIFY", "RESPOND"])
    elif result.needs_clarification:
        stages_done = stages[:5]
    else:
        stages_done.append("RESPOND")

    reply = result.message_for_user or (
        f"{success_prefix}." if verified else "I couldn't complete that."
    )
    # Presentation only — never encode a new command
    return PlanResult(
        reply=reply,
        success=result.success,
        verified=verified,
        needs_clarification=result.needs_clarification,
        plan=plan,
        tool_trace=[{"tool": plan.workflow, "arguments": plan.tool_args, "result": body}],
        stages_completed=stages_done,
        presentation_only=True,
    )
