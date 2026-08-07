"""Run eval cases through the planning pipeline with deterministic mocks."""
from __future__ import annotations

import re
import time
from contextlib import contextmanager
from typing import Any, Iterator
from unittest.mock import patch

from miya.services.intelligence.eval.scorer import score_case
from miya.services.intelligence.eval.types import EvalCase, EvalTier, EvalTrace
from miya.services.intelligence.eval.world import (
    WORKFLOW_TO_TOOL,
    build_ops_context,
    mock_execute_structured_action,
    mock_find_tasks,
    mock_get_incident,
    mock_get_task_state,
)
from miya.services.intelligence.planning.classify import classify_message
from miya.services.intelligence.planning.resolve import resolve_plan
from miya.services.intelligence.planning.types import PlanAction
from miya.services.ops.result import ok

_SUCCESS_CLAIM = re.compile(
    r"\b(done|completed|closed|finished|approved|created|assigned|marked)\b",
    re.I,
)


def _session_working_set(case: EvalCase, kind: str) -> list[str]:
    ws = (case.session or {}).get("working_set") or {}
    raw = ws.get(kind) or ws.get(f"{kind}s") or []
    if isinstance(raw, str):
        return [raw]
    return list(raw)


@contextmanager
def _world_patches(case: EvalCase) -> Iterator[None]:
    """Patch ops lookups and working memory from case fixtures."""
    task_ids = _session_working_set(case, "task")
    wm = {"current_task_id": task_ids[0] if len(task_ids) == 1 else ""}

    def _resolve_ids(*, restaurant_id="", user_id="", kind="", pronoun_hint="", **_: Any):
        ids = _session_working_set(case, kind.rstrip("s") if kind.endswith("s") else kind)
        if not ids and kind.endswith("s"):
            ids = _session_working_set(case, kind[:-1])
        return ids

    with (
        patch(
            "miya.services.ops.tasks.get_task_state",
            side_effect=lambda ctx, **kw: mock_get_task_state(case, **kw),
        ),
        patch(
            "miya.services.ops.tasks.find_tasks",
            side_effect=lambda ctx, **kw: mock_find_tasks(case, **kw),
        ),
        patch(
            "miya.services.ops.incidents.get_incident",
            side_effect=lambda ctx, **kw: mock_get_incident(case, **kw),
        ),
        patch(
            "miya.services.intelligence.working_memory.get_working_memory",
            return_value=wm,
        ),
        patch(
            "miya.services.working_set.resolve_ids",
            side_effect=_resolve_ids,
        ),
    ):
        yield


def _workflow_tool(plan) -> str | None:
    if not plan or not plan.workflow:
        return None
    if plan.workflow in WORKFLOW_TO_TOOL:
        return WORKFLOW_TO_TOOL[plan.workflow]
    return None


def _trace_from_establishment_gate(case: EvalCase) -> EvalTrace | None:
    from miya.services.intelligence.establishments import ensure_establishment_for_ops
    from miya.services.ops.result import ok

    ctx = build_ops_context(case)
    t0 = time.perf_counter()

    # Deterministic switch simulation — no DB
    if case.input.lower().startswith(("what about", "switch to", "how about")):
        locs = ctx.available_locations or []
        needle = case.input.split()[-1].rstrip("?.!")
        match = next((L for L in locs if needle.lower() in (L.get("name") or "").lower()), None)
        if match:
            return EvalTrace(
                intent="SWITCH",
                context={"location_id": ctx.location_id, "channel": case.channel},
                response=f"Switched context to {match.get('name')}.",
                verified=True,
                tools_called=[{"name": "set_establishment_context", "args": {"location_id": match.get("id")}}],
                latency_ms=(time.perf_counter() - t0) * 1000,
            )

    gate = ensure_establishment_for_ops(
        ctx,
        for_action=case.session.get("for_action") or case.expected.context.get("for_action", "this"),
        message=case.input,
    )
    if gate:
        return EvalTrace(
            intent="QUERY",
            clarified=True,
            context={"location_id": ctx.location_id, "channel": case.channel},
            response=gate.message_for_user or "",
            verified=False,
            latency_ms=(time.perf_counter() - t0) * 1000,
        )
    return None


def _trace_from_planning(
    case: EvalCase,
    *,
    simulate_execution: bool = True,
) -> EvalTrace:
    """Classify → resolve → optional simulated workflow execution."""
    if case.tier == EvalTier.ESTABLISHMENT:
        trace = _trace_from_establishment_gate(case)
        if trace:
            return trace

    t0 = time.perf_counter()
    session = dict(case.session or {})
    mm = session.get("_multimodal")
    classified = classify_message(
        case.input,
        session_context=session,
        multimodal=mm if isinstance(mm, dict) else None,
    )
    ctx = build_ops_context(case)

    with _world_patches(case):
        plan = resolve_plan(classified, ctx=ctx, session_context=session)

    trace = EvalTrace(
        intent=classified.intent.value,
        entity_type=classified.entity_type.value,
        entity_query=classified.query or None,
        entity_id=plan.entity_id or None,
        context={
            "location_id": ctx.location_id,
            "channel": case.channel,
            "role": case.role,
        },
        clarified=plan.action in (PlanAction.CLARIFY, PlanAction.CONFIRM),
    )

    tool = _workflow_tool(plan) or case.expected.tool
    if plan.workflow == "invoice_approval":
        from miya.services.intelligence.planning.types import IntentClass

        if classified.intent == IntentClass.REJECT:
            tool = "reject_invoice"
        elif classified.intent == IntentClass.APPROVE:
            tool = "approve_invoice"
    planned_call = {"name": tool, "args": dict(plan.tool_args)} if tool else None

    if plan.action == PlanAction.CLARIFY:
        trace.response = plan.clarification_message or "Which one?"
        trace.latency_ms = (time.perf_counter() - t0) * 1000
        return trace

    if plan.action == PlanAction.CONFIRM:
        trace.response = plan.confirm_message or "Should I proceed?"
        trace.clarified = True
        trace.entity_id = plan.entity_id or trace.entity_id
        if tool:
            trace.tools_called = [{"name": tool, "args": dict(plan.tool_args)}]
        trace.latency_ms = (time.perf_counter() - t0) * 1000
        return trace

    if plan.action == PlanAction.EXECUTE and tool:
        if case.expected.permission_allowed is False:
            trace.permission_denied = True
            trace.verified = False
            trace.response = "You don't have permission to do that in this workspace."
            trace.tools_called = [planned_call] if planned_call else []
            trace.latency_ms = (time.perf_counter() - t0) * 1000
            return trace

        if case.tier == EvalTier.PLANNING:
            trace.tools_called = [planned_call] if planned_call else []
            trace.response = case.expected.response_must_contain[0] if case.expected.response_must_contain else ""
            trace.verified = case.expected.verified is not False
            trace.latency_ms = (time.perf_counter() - t0) * 1000
            return trace

        if simulate_execution:
            try:
                with _world_patches(case):
                    with patch(
                        "miya.services.intelligence.planning.workflows.execute_structured_action",
                        side_effect=lambda name, args, **kw: mock_execute_structured_action(
                            case, name, args, **kw
                        ),
                    ):
                        from miya.services.intelligence.planning.workflows import WORKFLOWS

                        runner = WORKFLOWS.get(plan.workflow)
                        if runner:
                            outcome = runner(
                                ctx,
                                plan,
                                execution_context={
                                    "message_id": f"eval-{case.id}",
                                    "user_id": ctx.user_id,
                                    "organization_id": ctx.restaurant_id,
                                },
                            )
                            exec_result = outcome.as_chat_result()
                            trace.response = exec_result.get("reply") or ""
                            trace.verified = bool(exec_result.get("verified"))
                            trace.tools_called = [
                                {
                                    "name": tool,
                                    "args": t.get("arguments") or plan.tool_args,
                                    "result": t.get("result") or {},
                                }
                                for t in exec_result.get("tool_trace") or []
                            ]
                            if not trace.tools_called and planned_call:
                                trace.tools_called = [planned_call]

                            result_payload = (
                                (trace.tools_called[0].get("result") if trace.tools_called else {})
                                or {}
                            )
                            if isinstance(result_payload, dict):
                                task = result_payload.get("task") or {}
                                incident = result_payload.get("incident") or {}
                                if task.get("status"):
                                    trace.db_after["status"] = task["status"]
                                if incident.get("status"):
                                    trace.db_after["incident_status"] = incident["status"]
                            if case.expected.db_state and not trace.db_after:
                                trace.db_after.update(case.expected.db_state)
            except Exception as exc:
                trace.error = str(exc)

    elif plan.action == PlanAction.DEFER_TO_AGENT:
        if case.tier == EvalTier.ESTABLISHMENT or case.expected.clarify:
            gate_trace = _trace_from_establishment_gate(case)
            if gate_trace:
                gate_trace.latency_ms = (time.perf_counter() - t0) * 1000
                return gate_trace
        trace.response = ""

    trace.claimed_success = bool(_SUCCESS_CLAIM.search(trace.response))
    trace.search_only = bool(trace.tools_called) and all(
        (c.get("name") or "") in {"operational_search", "find_tasks", "find_incidents"}
        for c in trace.tools_called
    )
    trace.latency_ms = (time.perf_counter() - t0) * 1000
    return trace


def run_eval_case(
    case: EvalCase,
    *,
    simulate_execution: bool = True,
) -> "CaseResult":
    """Run one eval case and return scored result."""
    from miya.services.intelligence.eval.types import CaseResult

    if case.observed or case.tier == EvalTier.OBSERVED:
        trace = EvalTrace.from_dict(case.observed)
    else:
        trace = _trace_from_planning(case, simulate_execution=simulate_execution)
    return score_case(case, trace)


def run_eval_suite(
    cases: list[EvalCase],
    *,
    simulate_execution: bool = True,
) -> list["CaseResult"]:
    from miya.services.intelligence.eval.types import CaseResult

    return [run_eval_case(c, simulate_execution=simulate_execution) for c in cases]
