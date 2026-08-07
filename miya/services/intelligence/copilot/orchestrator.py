"""
Phase 10 — Miya Operational Copilot orchestrator.

Integrates Phases 1–9 into one coherent pipeline:

  UNDERSTAND → CONTEXT → (RETRIEVE/REMEMBER/REASON/SEARCH/PLAN)
  → AUTHORIZE → EXECUTE → VERIFY → RECORD → NOTIFY → RESPOND

Miya is the operational intelligence layer — not a chatbot.
Optimize for: correctness, reliability, truthfulness, context, action, safety, speed, traceability.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from miya.services.intelligence.copilot.authorize import authorize_mutation
from miya.services.intelligence.copilot.envelope import notify_after_mutation, record_turn
from miya.services.intelligence.copilot.types import CopilotResult, CopilotStage
from miya.services.intelligence.copilot.understand import (
    is_briefing_query,
    is_mutation_intent,
    is_operational_search_query,
    routing_hint,
    understand_turn,
)
from miya.services.intelligence.planning.compound import detect_compound_intent
from miya.services.intelligence.planning.types import IntentClass
from miya.services.intelligence.turn_trace import TurnTraceTimer, new_turn_trace

logger = logging.getLogger("miya.intelligence.copilot")


def run_copilot_turn(
    *,
    user,
    user_message: str,
    enriched_message: str,
    session_context: dict[str, Any],
    restaurant,
    channel: str = "dashboard",
    access_token: str | None = None,
    history: list[dict[str, str]] | None = None,
) -> CopilotResult | None:
    """
    Unified operational copilot routing.
    Returns CopilotResult if handled, None to defer to late fast paths / agent loop.
    """
    stages: list[str] = []
    mm = session_context.get("_multimodal")
    message = enriched_message or user_message or ""
    session_context.setdefault("channel", channel)

    exec_ctx = _execution_context(session_context, channel)
    trace = new_turn_trace(
        message_id=str(exec_ctx.get("message_id") or ""),
        conversation_id=str(exec_ctx.get("conversation_id") or ""),
        user_id=str(exec_ctx.get("user_id") or ""),
        tenant_id=str(exec_ctx.get("organization_id") or ""),
        establishment_id=str(exec_ctx.get("establishment_id") or ""),
        channel=channel,
    )
    timer = TurnTraceTimer(trace)

    # ── UNDERSTAND ────────────────────────────────────────────────────────
    classified = understand_turn(
        message,
        session_context=session_context,
        multimodal=mm if isinstance(mm, dict) else None,
        channel=channel,
    )
    stages.append(CopilotStage.UNDERSTAND.value)
    hint = routing_hint(user_message or message, classified)
    trace.intent = classified.intent.value
    trace.entity_type = classified.entity_type.value if classified.entity_type else ""
    trace.routing_hint = hint

    compound = detect_compound_intent(message, classified)
    if compound:
        session_context["_compound_plan"] = compound.to_dict()
        trace.compound = True
        trace.plan_workflow = "compound"

    # ── CONTEXT ───────────────────────────────────────────────────────────
    from miya.services.ops import build_ops_context

    ops_ctx = build_ops_context(
        user=user,
        restaurant=restaurant,
        session_context=session_context,
    )
    stages.extend([CopilotStage.CONTEXT.value, CopilotStage.REMEMBER.value])

    from miya.services.intelligence.pending_mutation import hydrate_pending_task_mutation

    hydrate_pending_task_mutation(
        user=user,
        restaurant=restaurant,
        session_context=session_context,
    )

    # Resume COMPLETE/ASSIGN after establishment clarification (Phase 12 follow-up).
    resumed = _try_pending_task_mutation_resume(
        message=user_message or message,
        classified=classified,
        ops_ctx=ops_ctx,
        session_context=session_context,
        exec_ctx=exec_ctx,
        user=user,
        restaurant=restaurant,
        history=history,
    )
    if resumed:
        resumed.stages_completed = stages + resumed.stages_completed
        return _finish_copilot_turn(trace, timer, resumed)

    # ── Establishment switch / multi-est gate (Phase 8) ───────────────────
    est = _try_establishment(
        user_message or message,
        ops_ctx,
        session_context,
        exec_ctx,
        classified=classified,
    )
    if est:
        est.stages_completed = stages + est.stages_completed
        return _finish_copilot_turn(trace, timer, est)

    # ── Proactive handle ("Handle the invoices.") ─────────────────────────
    proactive = _try_proactive_handle(user, user_message or message, channel, restaurant, exec_ctx)
    if proactive:
        proactive.stages_completed = stages + proactive.stages_completed
        return _finish_copilot_turn(trace, timer, proactive)

    # ── Briefing / "What needs my attention?" ─────────────────────────────
    # Entity-specific "what happened to X" must NOT hijack to daily briefing.
    if is_briefing_query(user_message or message) and not is_operational_search_query(
        user_message or message, classified
    ):
        brief = _try_briefing(user, restaurant, exec_ctx)
        if brief:
            brief.stages_completed = stages + brief.stages_completed
            return _finish_copilot_turn(trace, timer, brief)

    # ── AUTHORIZE (mutations — before routing) ────────────────────────────
    if is_mutation_intent(classified) and ops_ctx is not None:
        denied = authorize_mutation(classified, ops_ctx)
        if denied:
            stages.append(CopilotStage.AUTHORIZE.value)
            record_turn(
                handler="authorize_denied",
                intent=classified.intent.value,
                tool="",
                arguments={"message": user_message},
                result=denied.as_tool_response(),
                execution_context=exec_ctx,
            )
            stages.append(CopilotStage.RECORD.value)
            stages.append(CopilotStage.RESPOND.value)
            return _finish_copilot_turn(
                trace,
                timer,
                CopilotResult(
                    reply=denied.message_for_user or "You don't have permission.",
                    success=False,
                    tool_trace=[],
                    stages_completed=stages,
                    handler="authorize_denied",
                    needs_clarification=denied.needs_clarification,
                ),
                outcome="denied",
            )

    # ── COMPOUND EXECUTION (multi-step canonical plan) ────────────────────
    if trace.compound and session_context.get("_compound_plan") and is_mutation_intent(classified):
        compound_result = _try_compound_execution(
            message=message,
            classified=classified,
            ops_ctx=ops_ctx,
            session_context=session_context,
            exec_ctx=exec_ctx,
            compound_data=session_context["_compound_plan"],
        )
        if compound_result:
            compound_result.stages_completed = stages + compound_result.stages_completed
            return _finish_copilot_turn(trace, timer, compound_result)

    # ── PLAN + EXECUTE (mutations & deterministic workflows) BEFORE search ─
    if is_mutation_intent(classified) or _planning_handles(classified):
        planned = _try_planning(
            message=message,
            user=user,
            session_context=session_context,
            restaurant=restaurant,
            multimodal=mm,
            exec_ctx=exec_ctx,
            ops_ctx=ops_ctx,
            classified=classified,
        )
        if planned:
            planned.stages_completed = stages + planned.stages_completed
            return _finish_copilot_turn(trace, timer, planned)

    # ── SEARCH (read-only — never steals mutations) ───────────────────────
    if is_operational_search_query(user_message or message, classified):
        found = _try_search(
            user=user,
            query=user_message or message,
            restaurant=restaurant,
            session_context=session_context,
            channel=channel,
            exec_ctx=exec_ctx,
            classified=classified,
        )
        if found:
            found.stages_completed = stages + found.stages_completed
            return _finish_copilot_turn(trace, timer, found)

    # ── Staff lookup via planning (QUERY + staff) ─────────────────────────
    if classified.intent == IntentClass.QUERY and classified.entity_type.value == "staff":
        planned = _try_planning(
            message=message,
            user=user,
            session_context=session_context,
            restaurant=restaurant,
            multimodal=mm,
            exec_ctx=exec_ctx,
            ops_ctx=ops_ctx,
            classified=classified,
        )
        if planned:
            planned.stages_completed = stages + planned.stages_completed
            return _finish_copilot_turn(trace, timer, planned)

    logger.debug("copilot defer hint=%s intent=%s", hint, classified.intent.value)
    _finish_copilot_turn(trace, timer, None, outcome="defer")
    return None


def _finish_copilot_turn(
    trace,
    timer: TurnTraceTimer,
    result: CopilotResult | None,
    *,
    outcome: str = "",
) -> CopilotResult | None:
    """Emit Phase 12 turn trace without logging message content."""
    if result:
        trace.handler = result.handler or ""
        trace.verified = result.verified
        trace.tools_selected = [
            str(t.get("tool") or "") for t in (result.tool_trace or []) if t.get("tool")
        ]
        trace.stages_completed = list(result.stages_completed or [])
        if not outcome:
            if result.needs_clarification:
                outcome = "clarify"
            elif result.success:
                outcome = "success"
            else:
                outcome = "failed"
    elif not outcome:
        outcome = "defer"
    timer.finish(outcome=outcome)
    return result


def _planning_handles(classified) -> bool:
    """Intents the planning engine owns (including RETRIEVE document/incident)."""
    if classified.intent in (
        IntentClass.RETRIEVE,
        IntentClass.REMIND,
        IntentClass.SCHEDULE,
    ):
        return True
    if classified.intent == IntentClass.CREATE and classified.entity_type.value in (
        "incident",
        "invoice",
    ):
        return True
    return False


def _execution_context(session_context: dict, channel: str) -> dict[str, Any]:
    return {
        "message_id": session_context.get("_pipeline_message_id")
        or session_context.get("message_id")
        or "",
        "conversation_id": session_context.get("_pipeline_conversation_id")
        or session_context.get("conversation_id")
        or "",
        "user_id": str(session_context.get("user_id") or ""),
        "organization_id": str(session_context.get("restaurant_id") or ""),
        "establishment_id": str(session_context.get("location_id") or ""),
        "establishment_name": str(session_context.get("location_name") or ""),
        "channel": channel,
    }


def _try_establishment(
    message: str,
    ops_ctx,
    session_context: dict,
    exec_ctx: dict,
    *,
    classified=None,
) -> CopilotResult | None:
    from miya.services.intelligence.copilot.understand import is_mutation_intent
    from miya.services.intelligence.establishments import (
        ensure_establishment_for_ops,
        try_establishment_switch,
    )

    if ops_ctx is None:
        return None
    try:
        switched = try_establishment_switch(ops_ctx, message)
        if switched is not None:
            patch = (switched.data or {}).get("session_patch") or {}
            if switched.success and patch:
                session_context["location_id"] = patch.get("location_id")
                session_context["location_name"] = patch.get("location_name")
            record_turn(
                handler="establishment_switch",
                intent="SWITCH",
                tool="set_establishment_context",
                arguments={"message": message},
                result=switched.as_tool_response(),
                execution_context=exec_ctx,
            )
            return CopilotResult(
                reply=switched.message_for_user or "",
                success=switched.success,
                verified=bool(switched.verified),
                tool_trace=[
                    {"tool": "set_establishment_context", "result": switched.as_tool_response()}
                ],
                stages_completed=[
                    CopilotStage.AUTHORIZE.value,
                    CopilotStage.EXECUTE.value,
                    CopilotStage.RECORD.value,
                    CopilotStage.RESPOND.value,
                ],
                handler="establishment_switch",
                meta={"establishment_switch": True},
            )

        # Mutations carry their own establishment clarify in planning — do not block here.
        if classified is not None and is_mutation_intent(classified):
            return None

        gate = ensure_establishment_for_ops(
            ops_ctx,
            for_action="today's operations",
            message=message,
        )
        if gate is not None and gate.needs_clarification:
            record_turn(
                handler="establishment_clarify",
                intent="QUERY",
                tool="",
                arguments={"message": message},
                result=gate.as_tool_response(),
                execution_context=exec_ctx,
            )
            return CopilotResult(
                reply=gate.message_for_user or "Which establishment do you mean?",
                success=False,
                tool_trace=[],
                stages_completed=[CopilotStage.RESPOND.value, CopilotStage.RECORD.value],
                handler="establishment_clarify",
                needs_clarification=True,
                needs_establishment=True,
            )
    except Exception:
        logger.exception("establishment handler failed")
    return None


def _try_pending_task_mutation_resume(
    *,
    message: str,
    classified,
    ops_ctx,
    session_context: dict,
    exec_ctx: dict,
    user,
    restaurant,
    history: list[dict[str, str]] | None = None,
) -> CopilotResult | None:
    from miya.services.intelligence.pending_mutation import try_resume_pending_task_mutation

    try:
        return try_resume_pending_task_mutation(
            message=message,
            classified=classified,
            ops_ctx=ops_ctx,
            session_context=session_context,
            exec_ctx=exec_ctx,
            user=user,
            restaurant=restaurant,
            history=history,
        )
    except Exception:
        logger.exception("pending task mutation resume failed")
        return None


def _try_proactive_handle(user, message: str, channel: str, restaurant, exec_ctx: dict):
    from miya.services.intelligence.proactive import try_handle_briefing_request

    try:
        handled = try_handle_briefing_request(
            user=user,
            message=message,
            channel=channel,
            restaurant=restaurant,
        )
        if not handled:
            return None
        record_turn(
            handler="proactive_handle",
            intent="HANDLE",
            tool="proactive_workflow",
            arguments={"message": message},
            result={"success": handled.get("success"), "reply": handled.get("reply")},
            execution_context=exec_ctx,
        )
        return CopilotResult(
            reply=handled.get("reply") or "",
            success=bool(handled.get("success", True)),
            verified=bool(handled.get("verified")),
            tool_trace=handled.get("tool_trace") or [],
            stages_completed=[
                CopilotStage.PLAN.value,
                CopilotStage.EXECUTE.value,
                CopilotStage.VERIFY.value,
                CopilotStage.RECORD.value,
                CopilotStage.RESPOND.value,
            ],
            handler="proactive_handle",
            needs_clarification=bool(handled.get("needs_clarification")),
            meta={"proactive_handle": handled.get("proactive_handle")},
        )
    except Exception:
        logger.exception("proactive handle failed")
        return None


def _try_briefing(user, restaurant, exec_ctx: dict) -> CopilotResult | None:
    from miya.services.intelligence.proactive import on_demand_briefing

    try:
        brief = on_demand_briefing(user=user, restaurant=restaurant, period="morning")
        if not brief.get("reply"):
            return None
        record_turn(
            handler="daily_briefing",
            intent="BRIEFING",
            tool="on_demand_briefing",
            arguments={},
            result={"success": True, "reply_len": len(brief.get("reply") or "")},
            execution_context=exec_ctx,
        )
        return CopilotResult(
            reply=brief["reply"],
            success=True,
            tool_trace=[],
            stages_completed=[
                CopilotStage.RETRIEVE.value,
                CopilotStage.REASON.value,
                CopilotStage.SUMMARIZE.value,
                CopilotStage.RECORD.value,
                CopilotStage.RESPOND.value,
            ],
            handler="daily_briefing",
            meta={"daily_ops_briefing": True},
        )
    except Exception:
        logger.exception("briefing failed")
        return None


def _try_compound_execution(
    *,
    message: str,
    classified,
    ops_ctx,
    session_context: dict,
    exec_ctx: dict,
    compound_data: dict,
) -> CopilotResult | None:
    from miya.services.intelligence.planning.compound_execute import (
        compound_plan_from_dict,
        execute_compound_plan,
    )

    if ops_ctx is None:
        return None
    plan = compound_plan_from_dict(compound_data)
    if plan is None:
        return None

    outcome = execute_compound_plan(
        plan,
        classified=classified,
        ctx=ops_ctx,
        execution_context=exec_ctx,
    )

    record_turn(
        handler="compound_execution",
        intent=classified.intent.value,
        tool="compound_plan",
        arguments={"message": message, "steps": len(plan.steps)},
        result=outcome.to_dict(),
        execution_context=exec_ctx,
    )

    stages = [
        CopilotStage.PLAN.value,
        CopilotStage.AUTHORIZE.value,
        CopilotStage.EXECUTE.value,
    ]
    if outcome.verified:
        stages.append(CopilotStage.VERIFY.value)
    if any(s.action == "notify_manager_urgent" for s in plan.steps):
        stages.append(CopilotStage.NOTIFY.value)
    stages.extend([CopilotStage.RECORD.value, CopilotStage.RESPOND.value])

    return CopilotResult(
        reply=outcome.reply,
        success=outcome.success,
        verified=outcome.verified,
        tool_trace=outcome.tool_trace,
        stages_completed=stages,
        handler="compound_execution",
        meta={"compound_execution": outcome.to_dict()},
    )


def _try_planning(
    *,
    message: str,
    user,
    session_context: dict,
    restaurant,
    multimodal,
    exec_ctx: dict,
    ops_ctx,
    classified,
) -> CopilotResult | None:
    from miya.services.intelligence.planning import try_planning_engine

    t0 = time.perf_counter()
    try:
        planned = try_planning_engine(
            user_message=message,
            user=user,
            session_context=session_context,
            restaurant=restaurant,
            multimodal=multimodal if isinstance(multimodal, dict) else None,
        )
        if not planned:
            return None

        elapsed = (time.perf_counter() - t0) * 1000
        tool_trace = planned.get("tool_trace") or []
        verified = bool(planned.get("verified"))
        success = bool(planned.get("success"))

        record_turn(
            handler="planning_engine",
            intent=classified.intent.value,
            tool=(tool_trace[0].get("tool") if tool_trace else "planning"),
            arguments={"message": message},
            result={
                "success": success,
                "verified": verified,
                "needs_clarification": planned.get("needs_clarification"),
            },
            execution_context=exec_ctx,
            elapsed_ms=elapsed,
        )

        stages = [
            CopilotStage.PLAN.value,
            CopilotStage.EXECUTE.value,
        ]
        if verified:
            stages.extend(
                [
                    CopilotStage.VERIFY.value,
                    CopilotStage.NOTIFY.value,
                ]
            )
        stages.extend([CopilotStage.RECORD.value, CopilotStage.RESPOND.value])

        if ops_ctx and verified and tool_trace:
            from miya.services.ops.result import OpsResult as _OpsResult

            notify_after_mutation(
                ctx=ops_ctx,
                result=_OpsResult(
                    success=success,
                    verified=verified,
                    data=(tool_trace[0].get("result") or {}) if tool_trace else {},
                ),
                channel=exec_ctx.get("channel") or "dashboard",
            )

        return CopilotResult(
            reply=planned.get("reply") or "",
            success=success,
            verified=verified,
            tool_trace=tool_trace,
            stages_completed=stages,
            handler="planning_engine",
            needs_clarification=bool(planned.get("needs_clarification")),
            needs_confirmation=bool(planned.get("needs_confirmation")),
            meta={
                "planning_engine": True,
                "planning": planned.get("planning"),
                "multimodal": planned.get("multimodal"),
            },
        )
    except Exception:
        logger.exception("planning handler failed")
        return None


def _try_search(
    *,
    user,
    query: str,
    restaurant,
    session_context: dict,
    channel: str,
    exec_ctx: dict,
    classified,
) -> CopilotResult | None:
    from miya.services.intelligence.search import operational_search

    t0 = time.perf_counter()
    try:
        found = operational_search(
            user=user,
            query=query,
            restaurant=restaurant,
            session_context=session_context,
            channel=channel,
        )
        if not (found.success and (found.hits or found.reply)):
            return None
        elapsed = (time.perf_counter() - t0) * 1000
        record_turn(
            handler="operational_search",
            intent=classified.intent.value,
            tool="operational_search",
            arguments={"query": query},
            result={"success": True, "hit_count": len(found.hits), "mode": found.query.mode.value},
            execution_context=exec_ctx,
            elapsed_ms=elapsed,
        )
        return CopilotResult(
            reply=found.reply,
            success=True,
            verified=bool(found.scoped),
            tool_trace=[
                {
                    "tool": "operational_search",
                    "mode": found.query.mode.value,
                    "domain": found.query.domain.value,
                    "strategy": found.strategy,
                    "hit_count": len(found.hits),
                    "scoped": found.scoped,
                }
            ],
            stages_completed=[
                CopilotStage.SEARCH.value,
                CopilotStage.RETRIEVE.value,
                CopilotStage.RECORD.value,
                CopilotStage.RESPOND.value,
            ],
            handler="operational_search",
            meta={"operational_search": found.to_dict()},
        )
    except Exception:
        logger.exception("search handler failed")
        return None
