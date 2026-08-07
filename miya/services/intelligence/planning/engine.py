"""
Reasoning & Planning Engine (Phase 3).

UNDERSTAND → IDENTIFY → RETRIEVE → REASON → PLAN → EXECUTE → VERIFY → RESPOND

Deterministic workflows handle multi-step ops.
The final reply is PRESENTATION ONLY — it cannot trigger another operation.
"""
from __future__ import annotations

import logging
from typing import Any

from miya.services.intelligence.unified_understand import unified_understand
from miya.services.intelligence.planning.resolve import resolve_plan
from miya.services.intelligence.planning.types import (
    IntentClass,
    PlanAction,
    PlanResult,
)
from miya.services.intelligence.planning.workflows import WORKFLOWS
from miya.services.ops import build_ops_context

logger = logging.getLogger("miya.intelligence.planning")

# Intents handled by deterministic workflows (not unconstrained agent loop)
_WORKFLOW_INTENTS = frozenset(
    {
        IntentClass.COMPLETE,
        IntentClass.ASSIGN,
        IntentClass.ROUTE,
        IntentClass.APPROVE,
        IntentClass.REJECT,
        IntentClass.REMIND,
        IntentClass.SCHEDULE,
        IntentClass.RETRIEVE,
        IntentClass.UPLOAD,
        IntentClass.CREATE,
        IntentClass.QUERY,  # staff lookup (voice/text) via same engine
    }
)

_CREATE_ENTITIES = frozenset({"incident", "invoice"})


def try_planning_engine(
    *,
    user_message: str,
    user,
    session_context: dict[str, Any] | None,
    restaurant=None,
    multimodal: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Attempt deterministic planning/execution.
    Returns a chat-shaped dict, or None to defer to the unconstrained agent.

    Voice/image/PDF enter here as text + multimodal slots — never a separate brain.
    """
    ctx_dict = session_context or {}
    mm = multimodal if isinstance(multimodal, dict) else ctx_dict.get("_multimodal")
    if isinstance(mm, dict):
        ctx_dict["_multimodal"] = mm
    channel = str(ctx_dict.get("channel") or ctx_dict.get("input_channel") or "dashboard")
    classified = unified_understand(
        user_message,
        channel=channel,
        session_context=ctx_dict,
        multimodal=mm if isinstance(mm, dict) else None,
    )

    if classified.intent not in _WORKFLOW_INTENTS:
        return None
    # CREATE: incidents + invoice-from-media; other creates → agent
    if classified.intent == IntentClass.CREATE and classified.entity_type.value not in _CREATE_ENTITIES:
        return None
    # QUERY only for staff lookup in this engine
    if classified.intent == IntentClass.QUERY and classified.entity_type.value != "staff":
        return None

    ops_ctx = build_ops_context(
        user=user,
        restaurant=restaurant
        or getattr(user, "restaurant", None),
        session_context=ctx_dict,
    )
    if ops_ctx is None:
        return None

    plan = resolve_plan(classified, ctx=ops_ctx, session_context=ctx_dict)

    if plan.action == PlanAction.DEFER_TO_AGENT:
        return None

    if plan.action == PlanAction.CLARIFY:
        result = PlanResult(
            reply=plan.clarification_message or "I need a bit more detail — I won't guess.",
            success=False,
            needs_clarification=True,
            plan=plan,
            stages_completed=["UNDERSTAND", "IDENTIFY", "RETRIEVE", "REASON", "PLAN"],
        )
        return _stamp(result, ctx_dict)

    if plan.action == PlanAction.CONFIRM:
        result = PlanResult(
            reply=plan.confirm_message or "Should I proceed?",
            success=False,
            needs_confirmation=True,
            plan=plan,
            stages_completed=["UNDERSTAND", "IDENTIFY", "RETRIEVE", "REASON", "PLAN"],
        )
        return _stamp(result, ctx_dict)

    # EXECUTE via deterministic workflow
    runner = WORKFLOWS.get(plan.workflow)
    if not runner:
        return None

    exec_ctx = {
        "message_id": ctx_dict.get("_pipeline_message_id") or ctx_dict.get("message_id"),
        "conversation_id": ctx_dict.get("_pipeline_conversation_id")
        or ctx_dict.get("conversation_id"),
        "user_id": ctx_dict.get("user_id") or ops_ctx.user_id,
        "organization_id": ctx_dict.get("restaurant_id") or ops_ctx.restaurant_id,
        "establishment_id": ctx_dict.get("location_id") or ops_ctx.location_id,
        "establishment_name": ctx_dict.get("location_name") or ops_ctx.location_name,
        "channel": ctx_dict.get("channel") or ops_ctx.channel,
    }
    try:
        outcome = runner(ops_ctx, plan, execution_context=exec_ctx)
    except Exception:
        logger.exception("planning workflow %s failed", plan.workflow)
        return None

    stamped = _stamp(outcome, ctx_dict)
    if isinstance(mm, dict):
        stamped["multimodal"] = {
            "modalities": mm.get("modalities"),
            "primary_kind": mm.get("primary_kind"),
            "ocr_is_not_final_intelligence": True,
        }
    return stamped


def _stamp(result: PlanResult, session_context: dict[str, Any]) -> dict[str, Any]:
    body = result.as_chat_result()
    body["session_context"] = session_context
    # Hard guarantee: presentation cannot re-enter as command
    body["assistant_text_is_not_executable"] = True
    body["presentation_only"] = True
    return body
