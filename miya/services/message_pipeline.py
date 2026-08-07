"""
Miya message pipeline — strict separation of USER intent vs ASSISTANT text.

Architecture (NON-NEGOTIABLE):

  USER_MESSAGE
    → AGENT_REASONING
    → TOOL_CALL (structured)
    → TOOL_RESULT (structured)
    → FINAL_RESPONSE (natural language for humans only)
    → END

The FINAL_RESPONSE must NEVER be:
  - parsed for intent
  - fed back as a USER command
  - used to decide which task/incident/status to mutate
  - sent through another agent execution cycle

Mutations execute ONLY from structured tool calls (or authorized
USER_MESSAGE-stage fast paths that themselves emit structured tool args).
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from django.core.cache import cache

logger = logging.getLogger("miya.pipeline")

ALLOWED_HISTORY_ROLES = frozenset({"user", "assistant"})
# Roles that may NEVER initiate a new intent/mutation cycle
NON_INITIATING_ROLES = frozenset({"assistant", "tool", "system"})


class ExecutionStage(str, Enum):
    USER_MESSAGE = "USER_MESSAGE"
    AGENT_REASONING = "AGENT_REASONING"
    TOOL_CALL = "TOOL_CALL"
    TOOL_RESULT = "TOOL_RESULT"
    FINAL_RESPONSE = "FINAL_RESPONSE"
    END = "END"


@dataclass
class TurnContext:
    """One Miya turn — correlates logs without inferring from NL reply."""

    message_id: str
    conversation_id: str
    user_id: str = ""
    establishment_id: str = ""
    restaurant_id: str = ""
    channel: str = "dashboard"
    stage: ExecutionStage = ExecutionStage.USER_MESSAGE
    intent: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    final_response: str = ""
    terminated: bool = False

    def advance(self, stage: ExecutionStage) -> None:
        if self.terminated and stage not in (ExecutionStage.END,):
            raise RuntimeError(
                f"Miya turn {self.message_id} already terminated; "
                f"cannot advance to {stage.value}"
            )
        self.stage = stage

    def record_tool_call(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        tool_call_id: str | None = None,
    ) -> str:
        self.advance(ExecutionStage.TOOL_CALL)
        cid = (tool_call_id or "").strip() or f"tc-{uuid.uuid4().hex[:12]}"
        op_id = new_operation_id(tool_name, arguments, message_id=self.message_id)
        row = {
            "tool_call_id": cid,
            "tool_name": tool_name,
            "arguments": dict(arguments or {}),
            "operation_id": op_id,
        }
        self.tool_calls.append(row)
        log_turn_event(
            self,
            event="TOOL_CALL",
            tool_name=tool_name,
            tool_call_id=cid,
            operation_id=op_id,
            arguments=arguments,
        )
        return op_id

    def record_tool_result(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        operation_id: str,
        result: dict[str, Any] | Any,
    ) -> None:
        self.advance(ExecutionStage.TOOL_RESULT)
        summary = _tool_result_summary(result)
        self.tool_results.append(
            {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "operation_id": operation_id,
                "success": bool(isinstance(result, dict) and result.get("success")),
                "summary": summary,
            }
        )
        log_turn_event(
            self,
            event="TOOL_RESULT",
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            operation_id=operation_id,
            success=summary.get("success"),
            code=summary.get("code"),
        )

    def finalize(self, reply: str) -> dict[str, Any]:
        """Mark FINAL_RESPONSE → END. Reply is for humans only — not executable."""
        self.final_response = (reply or "").strip()
        self.advance(ExecutionStage.FINAL_RESPONSE)
        log_turn_event(
            self,
            event="FINAL_RESPONSE",
            reply_preview=self.final_response[:240],
            tool_call_count=len(self.tool_calls),
        )
        self.terminated = True
        self.stage = ExecutionStage.END
        log_turn_event(self, event="END")
        return {
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "execution_stage": ExecutionStage.END.value,
            "terminated": True,
            "tool_call_count": len(self.tool_calls),
            "operation_ids": [c.get("operation_id") for c in self.tool_calls],
        }


def new_message_id() -> str:
    return f"msg-{uuid.uuid4().hex}"


def new_conversation_id(channel: str, thread_key: str | None) -> str:
    base = (thread_key or "").strip() or uuid.uuid4().hex[:12]
    return f"conv-{(channel or 'dashboard').strip().lower()}-{base}"


def new_operation_id(tool_name: str, arguments: dict[str, Any] | None, *, message_id: str = "") -> str:
    raw = f"{message_id}|{tool_name}|{sorted((arguments or {}).items())}"
    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"op-{tool_name}-{digest}"


def begin_turn(
    *,
    user,
    channel: str,
    session_context: dict[str, Any] | None = None,
    inbound_message_id: str | None = None,
) -> TurnContext:
    ctx = session_context or {}
    thread = (
        ctx.get("thread_id")
        or ctx.get("whatsapp_session_id")
        or ctx.get("user_id")
        or getattr(user, "id", None)
    )
    return TurnContext(
        message_id=(inbound_message_id or "").strip() or new_message_id(),
        conversation_id=new_conversation_id(channel, str(thread) if thread else None),
        user_id=str(ctx.get("user_id") or getattr(user, "id", "") or ""),
        establishment_id=str(ctx.get("location_id") or ""),
        restaurant_id=str(ctx.get("restaurant_id") or ""),
        channel=(channel or "dashboard").strip().lower(),
        stage=ExecutionStage.USER_MESSAGE,
    )


def sanitize_history(history: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """
    Keep only user/assistant turns for LLM context.
    Never promote assistant/tool/system content into a synthetic user turn.
    """
    out: list[dict[str, str]] = []
    for turn in history or []:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "").strip().lower()
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        if role in NON_INITIATING_ROLES and role != "assistant":
            continue
        if role not in ALLOWED_HISTORY_ROLES:
            continue
        out.append({"role": role, "content": content})
    return out


def assert_user_initiated(stage: ExecutionStage, *, for_action: str) -> None:
    """Mutating fast paths may only run during USER_MESSAGE stage."""
    if stage != ExecutionStage.USER_MESSAGE:
        raise RuntimeError(
            f"Refusing {for_action}: mutations from assistant/tool text are forbidden "
            f"(stage={stage.value})."
        )


def assistant_text_must_not_execute(text: str | None) -> None:
    """Natural-language assistant replies are never executable commands."""
    _ = (text or "").strip()
    return None


def claim_mutation_once(
    operation_id: str,
    *,
    ttl_seconds: int = 120,
) -> bool:
    """
    Idempotency lock for state-changing ops.
    Returns True if this is the first claim (caller should execute).
    Returns False if a duplicate was seen within ttl (caller should skip mutate).
    """
    oid = (operation_id or "").strip()
    if not oid:
        return True
    return bool(cache.add(f"miya:mutation:{oid}", "1", ttl_seconds))


def log_turn_event(turn: TurnContext, *, event: str, **fields: Any) -> None:
    payload = {
        "event": event,
        "message_id": turn.message_id,
        "conversation_id": turn.conversation_id,
        "user_id": turn.user_id,
        "establishment_id": turn.establishment_id,
        "restaurant_id": turn.restaurant_id,
        "channel": turn.channel,
        "stage": turn.stage.value,
        "intent": turn.intent,
        **{k: v for k, v in fields.items() if v is not None},
    }
    logger.info("MIYA_PIPELINE %s", payload)


def _tool_result_summary(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"success": False, "code": "non_dict_result"}
    return {
        "success": bool(result.get("success")),
        "code": result.get("code") or result.get("error") or ("ok" if result.get("success") else "error"),
        "verified": result.get("verified"),
    }


def attach_pipeline_meta(result: dict[str, Any], turn: TurnContext, reply: str) -> dict[str, Any]:
    """Stamp pipeline metadata onto the chat result; finalize the turn."""
    meta = turn.finalize(reply)
    out = dict(result or {})
    out["reply"] = reply
    out["pipeline"] = meta
    out["message_id"] = turn.message_id
    out["conversation_id"] = turn.conversation_id
    out["execution_stage"] = ExecutionStage.END.value
    out["assistant_text_is_not_executable"] = True
    return out
