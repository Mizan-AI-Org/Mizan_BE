"""Execution / audit layer — correlate tool executions without storing secrets."""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("miya.intelligence.audit")


def record_audit(
    *,
    message_id: str = "",
    conversation_id: str = "",
    operation_id: str = "",
    user_id: str = "",
    organization_id: str = "",
    establishment_id: str = "",
    intent: str = "",
    tool: str = "",
    arguments: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    execution_time_ms: float | None = None,
    status: str = "",
) -> dict[str, Any]:
    row = {
        "message_id": message_id or None,
        "conversation_id": conversation_id or None,
        "operation_id": operation_id or None,
        "user_id": user_id or None,
        "organization_id": organization_id or None,
        "establishment_id": establishment_id or None,
        "intent": intent or None,
        "tool": tool or None,
        "arguments": _redact_args(arguments),
        "result_summary": _result_summary(result),
        "execution_time_ms": round(execution_time_ms, 2) if execution_time_ms is not None else None,
        "status": status or _status_from_result(result),
    }
    logger.info("MIYA_AUDIT %s", row)
    return row


def timed_call(fn, *args, **kwargs):
    start = time.perf_counter()
    out = fn(*args, **kwargs)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return out, elapsed_ms


def _redact_args(arguments: dict[str, Any] | None) -> dict[str, Any]:
    if not arguments:
        return {}
    blocked = {"password", "token", "access_token", "authorization", "ssn", "card_number"}
    out = {}
    for k, v in arguments.items():
        if str(k).startswith("_"):
            continue
        if str(k).lower() in blocked:
            continue  # never store secrets in audit
        elif isinstance(v, str) and len(v) > 500:
            out[k] = v[:500] + "…"
        else:
            out[k] = v
    return out


def _result_summary(result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"success": False}
    return {
        "success": bool(result.get("success")),
        "verified": result.get("verified"),
        "code": result.get("code") or result.get("error"),
        "operation": result.get("operation"),
        "needs_clarification": result.get("needs_clarification"),
        "deduplicated": result.get("deduplicated"),
    }


def _status_from_result(result: dict[str, Any] | None) -> str:
    if not isinstance(result, dict):
        return "error"
    if result.get("deduplicated"):
        return "deduplicated"
    if result.get("needs_clarification"):
        return "clarification"
    if result.get("success") and result.get("verified"):
        return "verified_success"
    if result.get("success"):
        return "success_unverified"
    return "failure"
