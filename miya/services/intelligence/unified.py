"""
Phase 5 — Unified Experience.

All channels (DASHBOARD, WHATSAPP, MOBILE, VOICE) share one stack:

  Context Engine → Structured Actions → Ops Services → DB → Events → Memory → Notifications

Channel adapters MUST NOT implement business logic. They only:
  - normalize the channel label
  - build OpsContext / ExecutionContext
  - call execute_structured_action (or run_miya_chat for NL)

Reads always return CURRENT DATABASE STATE — never a channel-local cache.
"""
from __future__ import annotations

import logging
from typing import Any

from miya.services.intelligence.actions import execute_structured_action
from miya.services.ops import build_ops_context
from miya.services.ops.context import OpsContext
from miya.services.ops.result import OpsResult, fail

logger = logging.getLogger("miya.intelligence.unified")

CANONICAL_CHANNELS = frozenset({"dashboard", "whatsapp", "mobile", "voice"})

# Alias map — adapters may send product names; we normalize.
_CHANNEL_ALIASES = {
    "dashboard": "dashboard",
    "web": "dashboard",
    "widget": "dashboard",
    "whatsapp": "whatsapp",
    "wa": "whatsapp",
    "mobile": "mobile",
    "app": "mobile",
    "ios": "mobile",
    "android": "mobile",
    "voice": "voice",
    "dashboard_voice": "voice",
}


def normalize_channel(channel: str | None) -> str:
    raw = (channel or "dashboard").strip().lower()
    return _CHANNEL_ALIASES.get(raw, raw if raw in CANONICAL_CHANNELS else "dashboard")


def ops_context_for_channel(
    *,
    user,
    channel: str,
    restaurant=None,
    session_hint: dict[str, Any] | None = None,
) -> OpsContext | None:
    """Build the shared OpsContext — same permissions/DB scope for every channel."""
    ch = normalize_channel(channel)
    hint = dict(session_hint or {})
    hint["channel"] = ch
    if restaurant is None:
        restaurant = getattr(user, "restaurant", None)
    return build_ops_context(user=user, restaurant=restaurant, session_context=hint)


def execution_context_for_channel(
    *,
    user,
    channel: str,
    restaurant=None,
    message_id: str = "",
    conversation_id: str = "",
    session_hint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ch = normalize_channel(channel)
    hint = session_hint or {}
    rid = str(
        getattr(restaurant, "id", None)
        or hint.get("restaurant_id")
        or getattr(user, "restaurant_id", None)
        or ""
    )
    return {
        "message_id": message_id or f"{ch}:{getattr(user, 'id', '')}",
        "conversation_id": conversation_id or f"{ch}:{getattr(user, 'id', '')}",
        "user_id": str(getattr(user, "id", "") or ""),
        "organization_id": rid,
        "establishment_id": str(hint.get("location_id") or ""),
        "establishment_name": str(hint.get("location_name") or ""),
        "channel": ch,
    }


def apply_task_status(
    *,
    user,
    channel: str,
    status: str,
    task_id: str = "",
    q: str = "",
    title: str = "",
    restaurant=None,
    session_hint: dict[str, Any] | None = None,
    assignee_scope: bool | None = None,
    notify_managers: bool | None = None,
    message_id: str = "",
) -> OpsResult:
    """
    Canonical task status mutation for Dashboard / WhatsApp / Mobile / Voice.

    Never write Task.status directly from a channel adapter.
    """
    ch = normalize_channel(channel)
    ctx = ops_context_for_channel(
        user=user, channel=ch, restaurant=restaurant, session_hint=session_hint
    )
    if ctx is None:
        return fail(code="no_workspace", message="Workspace not linked.")

    # Defaults: staff channels scope to assignee; managers on dashboard use full resolve
    if assignee_scope is None:
        assignee_scope = ch in ("whatsapp", "mobile")
    if notify_managers is None:
        notify_managers = ch in ("whatsapp", "mobile") or (
            str(status).upper() in ("COMPLETED", "UNABLE_TO_COMPLETE", "DONE", "COMPLETE")
            and ch in ("dashboard", "voice")
        )

    action = "complete_task" if str(status).upper() in ("COMPLETED", "DONE", "COMPLETE", "FINISHED") else "update_task_status"
    args: dict[str, Any] = {
        "status": status,
        "task_id": task_id or "",
        "q": q or "",
        "title": title or "",
        "assignee_scope": assignee_scope,
        "notify_managers": notify_managers,
    }
    exec_ctx = execution_context_for_channel(
        user=user,
        channel=ch,
        restaurant=ctx.restaurant,
        message_id=message_id,
        session_hint=session_hint,
    )
    intent = "COMPLETE" if action == "complete_task" else "UPDATE"
    return execute_structured_action(
        action,
        args,
        ctx=ctx,
        execution_context=exec_ctx,
        intent=intent,
    )


def get_task_reality(
    *,
    user,
    channel: str,
    task_id: str = "",
    q: str = "",
    title: str = "",
    restaurant=None,
    session_hint: dict[str, Any] | None = None,
) -> OpsResult:
    """Read CURRENT DATABASE STATE for a task — same for every channel."""
    ch = normalize_channel(channel)
    ctx = ops_context_for_channel(
        user=user, channel=ch, restaurant=restaurant, session_hint=session_hint
    )
    if ctx is None:
        return fail(code="no_workspace", message="Workspace not linked.")
    return execute_structured_action(
        "get_current_task",
        {"task_id": task_id, "q": q or title, "title": title},
        ctx=ctx,
        execution_context=execution_context_for_channel(
            user=user, channel=ch, restaurant=ctx.restaurant, session_hint=session_hint
        ),
        intent="QUERY",
    )


def run_unified_miya(
    *,
    user,
    user_message: str,
    channel: str,
    access_token: str | None = None,
    history: list | None = None,
    preferred_restaurant_id: str | None = None,
    session_hint: dict[str, Any] | None = None,
    attachment_ids: list[str] | None = None,
    inbound_message_id: str | None = None,
) -> dict[str, Any]:
    """
    NL entry for every channel — always the same run_miya_chat brain.
    Voice STT / WhatsApp text / Dashboard / Mobile all land here.
    """
    from miya.services.agent import run_miya_chat

    ch = normalize_channel(channel)
    hint = dict(session_hint or {})
    hint["channel"] = ch
    if ch == "voice":
        hint["voice"] = True
    result = run_miya_chat(
        user=user,
        access_token=access_token,
        user_message=user_message,
        history=history,
        channel=ch if ch != "voice" else "voice",
        preferred_restaurant_id=preferred_restaurant_id,
        session_hint=hint,
        attachment_ids=attachment_ids,
        inbound_message_id=inbound_message_id,
    )
    if isinstance(result, dict):
        result["unified_channel"] = ch
        result["assistant_text_is_not_executable"] = True
    return result
