"""Resume task mutations after establishment clarification."""
from __future__ import annotations

import logging
import re
from typing import Any

from miya.services.intelligence.copilot.types import CopilotResult, CopilotStage
from miya.services.intelligence.copilot.envelope import record_turn
from miya.services.ops.context import OpsContext

logger = logging.getLogger("miya.intelligence.pending_mutation")

PENDING_WM_KEY = "pending_task_mutation"


def persist_pending_task_mutation(*, user, restaurant, payload: dict[str, Any]) -> None:
    from miya.services.intelligence.working_memory import update_working_memory

    update_working_memory(user=user, restaurant=restaurant, extra={PENDING_WM_KEY: payload})


def load_pending_task_mutation(*, user, restaurant) -> dict[str, Any] | None:
    try:
        from miya.models import WorkingMemorySnapshot

        snap = WorkingMemorySnapshot.objects.filter(
            restaurant=restaurant, user=user
        ).first()
        if not snap or not snap.extra:
            return None
        pending = snap.extra.get(PENDING_WM_KEY)
        return pending if isinstance(pending, dict) else None
    except Exception:
        logger.exception("load_pending_task_mutation failed")
        return None


def clear_pending_task_mutation(*, user, restaurant) -> None:
    try:
        from miya.models import WorkingMemorySnapshot
        from miya.services.intelligence.working_memory import update_working_memory

        snap = WorkingMemorySnapshot.objects.filter(
            restaurant=restaurant, user=user
        ).first()
        if not snap or not snap.extra or PENDING_WM_KEY not in snap.extra:
            return
        extra = dict(snap.extra)
        extra.pop(PENDING_WM_KEY, None)
        update_working_memory(user=user, restaurant=restaurant, extra=extra)
    except Exception:
        logger.exception("clear_pending_task_mutation failed")


def hydrate_pending_task_mutation(
    *,
    user,
    restaurant,
    session_context: dict[str, Any],
) -> None:
    if session_context.get("_pending_task_mutation"):
        return
    pending = load_pending_task_mutation(user=user, restaurant=restaurant)
    if pending:
        session_context["_pending_task_mutation"] = pending


def recover_pending_from_history(
    *,
    message: str,
    history: list[dict[str, str]] | None,
    ops_ctx: OpsContext,
) -> dict[str, Any] | None:
    """Rebuild pending mutation when user replies with an establishment name."""
    msg = (message or "").strip()
    if not msg or not history:
        return None

    last_assistant = ""
    for turn in reversed(history):
        role = (turn.get("role") or "").lower()
        content = (turn.get("content") or turn.get("text") or "").strip()
        if role == "assistant" and not last_assistant:
            last_assistant = content
        elif role == "user" and last_assistant:
            break

    if "which establishment" not in last_assistant.lower():
        return None

    from miya.services.ops.establishments import set_establishment_context

    switched = set_establishment_context(ops_ctx, q=msg)
    if not switched.success:
        return None

    task_pattern = re.compile(
        r"\b(close|complete|finish|mark)\b.*\btask\b|\btask\b.*\b(done|complete|close|finished)\b",
        re.I,
    )
    for turn in reversed(history):
        if (turn.get("role") or "").lower() != "user":
            continue
        content = (turn.get("content") or turn.get("text") or "").strip()
        if task_pattern.search(content):
            return {
                "raw_message": content,
                "query": _extract_task_query_from_message(content),
                "intent": "COMPLETE",
                "status_hint": "COMPLETED",
            }
    return None


def _extract_task_query_from_message(message: str) -> str:
    match = re.search(
        r"(?:close|complete|finish|mark)\s+(?:the\s+)?(.+?)(?:\s+task|\s*,|\s+its|\s+it's|\s+as\s+done|$)",
        message,
        re.I,
    )
    if match:
        return match.group(1).strip()
    return message.strip()


def try_resume_pending_task_mutation(
    *,
    message: str,
    classified,
    ops_ctx: OpsContext,
    session_context: dict[str, Any],
    exec_ctx: dict[str, Any],
    user,
    restaurant,
    history: list[dict[str, str]] | None = None,
) -> CopilotResult | None:
    """
    When the user clarifies establishment after a blocked COMPLETE/ASSIGN,
    re-run the stored mutation against the selected branch.
    """
    pending = session_context.get("_pending_task_mutation")
    if not isinstance(pending, dict):
        pending = recover_pending_from_history(
            message=message,
            history=history,
            ops_ctx=ops_ctx,
        )
        if pending:
            session_context["_pending_task_mutation"] = pending

    if not isinstance(pending, dict):
        return None

    from miya.services.ops.establishments import set_establishment_context

    switched = set_establishment_context(ops_ctx, q=message.strip())
    if switched.success:
        patch = (switched.data or {}).get("session_patch") or {}
        if patch.get("location_id"):
            session_context["location_id"] = patch["location_id"]
            session_context["location_name"] = patch.get("location_name")
            ops_ctx.location_id = str(patch["location_id"])
            ops_ctx.location_name = patch.get("location_name") or ops_ctx.location_name
    elif switched.needs_clarification:
        return None
    elif not ops_ctx.location_id:
        return None

    from miya.services.intelligence.planning import try_planning_engine

    resume_message = str(pending.get("raw_message") or message).strip()
    planned = try_planning_engine(
        user_message=resume_message,
        user=user,
        session_context=session_context,
        restaurant=restaurant,
    )
    session_context.pop("_pending_task_mutation", None)
    clear_pending_task_mutation(user=user, restaurant=restaurant)
    if not planned:
        return None

    record_turn(
        handler="pending_task_mutation_resume",
        intent=str(pending.get("intent") or classified.intent.value),
        tool=planned.get("handler") or "planning_engine",
        arguments={"resume_message": resume_message, "establishment": ops_ctx.location_name},
        result=planned,
        execution_context=exec_ctx,
    )
    return CopilotResult(
        reply=planned.get("reply") or "",
        success=bool(planned.get("success")),
        verified=planned.get("verified"),
        needs_clarification=bool(planned.get("needs_clarification")),
        tool_trace=planned.get("tool_trace") or [],
        stages_completed=[
            CopilotStage.CONTEXT.value,
            CopilotStage.PLAN.value,
            CopilotStage.EXECUTE.value,
            CopilotStage.RESPOND.value,
        ],
        handler="pending_task_mutation_resume",
        meta={"pending_task_mutation_resume": True},
    )
