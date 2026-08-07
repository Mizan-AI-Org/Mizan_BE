"""RECORD + NOTIFY — mandatory post-action envelope."""
from __future__ import annotations

import logging
from typing import Any

from miya.services.intelligence.audit import record_audit
from miya.services.ops.context import OpsContext
from miya.services.ops.result import OpsResult

logger = logging.getLogger("miya.intelligence.copilot")


def record_turn(
    *,
    handler: str,
    intent: str,
    tool: str,
    arguments: dict[str, Any] | None,
    result: dict[str, Any] | None,
    execution_context: dict[str, Any] | None,
    elapsed_ms: float | None = None,
) -> dict[str, Any]:
    """RECORD — audit every copilot handler outcome (read or write)."""
    exec_ctx = execution_context or {}
    return record_audit(
        message_id=str(exec_ctx.get("message_id") or ""),
        conversation_id=str(exec_ctx.get("conversation_id") or ""),
        user_id=str(exec_ctx.get("user_id") or ""),
        organization_id=str(exec_ctx.get("organization_id") or ""),
        establishment_id=str(exec_ctx.get("establishment_id") or ""),
        intent=intent or handler,
        tool=tool or handler,
        arguments=arguments,
        result=result,
        execution_time_ms=elapsed_ms,
    )


def notify_after_mutation(
    *,
    ctx: OpsContext,
    result: OpsResult,
    channel: str,
) -> bool:
    """
    NOTIFY — post-VERIFY notification hook.
    Domain-specific notify (task assign, etc.) still lives in ops services;
    this logs copilot-level notification intent for traceability.
    """
    if not result.success or not result.verified:
        return False
    data = result.data or {}
    if data.get("deduplicated"):
        return False
    op = data.get("operation") or ""
    logger.info(
        "MIYA_NOTIFY channel=%s operation=%s user=%s establishment=%s",
        channel,
        op,
        ctx.user_id,
        ctx.location_id,
    )
    return True
