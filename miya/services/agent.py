"""Miya conversational agent — OpenAI reasoning + Mizan tools + Fish Audio voice."""

from __future__ import annotations

import json
import logging
from typing import Any

import requests
from django.conf import settings

from .context import build_session_context, build_system_prompt
from .tools import execute_tool, serialize_tool_result, tools_for_user

logger = logging.getLogger(__name__)

MAX_TOOL_STEPS = 12


def _openai_chat(messages: list[dict[str, Any]], *, tools: list | None = None) -> dict[str, Any]:
    api_key = getattr(settings, "OPENAI_API_KEY", "") or ""
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    model = getattr(settings, "MIYA_CHAT_MODEL", "gpt-4o-mini")
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.25,
        "max_tokens": 1800,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=90,
    )
    if resp.status_code != 200:
        logger.warning("Miya OpenAI error %s: %s", resp.status_code, resp.text[:400])
        raise RuntimeError(f"OpenAI error {resp.status_code}")

    return resp.json()


def run_miya_chat(
    *,
    user,
    access_token: str | None,
    user_message: str,
    history: list[dict[str, str]] | None = None,
    channel: str = "dashboard",
    preferred_restaurant_id: str | None = None,
    session_hint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one Miya turn (may include multiple tool calls). Returns reply text + metadata."""
    session_context = build_session_context(
        user,
        channel=channel,
        preferred_restaurant_id=preferred_restaurant_id,
        session_hint=session_hint,
    )
    system_prompt = build_system_prompt(
        user,
        channel=channel,
        preferred_restaurant_id=preferred_restaurant_id,
        session_hint=session_hint,
    )

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for turn in history or []:
        role = turn.get("role")
        content = (turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_message.strip()})

    tool_trace: list[dict[str, Any]] = []
    tenant_rest = None
    rid = session_context.get("restaurant_id")
    if rid:
        from accounts.models import Restaurant

        tenant_rest = Restaurant.objects.filter(id=rid).first()
    active_tools = tools_for_user(user, restaurant=tenant_rest)

    for _ in range(MAX_TOOL_STEPS + 1):
        data = _openai_chat(messages, tools=active_tools or None)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            reply = (message.get("content") or "").strip()
            return {
                "reply": reply or "I'm here. What would you like me to help with?",
                "tool_trace": tool_trace,
                "session_context": session_context,
            }

        messages.append(message)

        for call in tool_calls:
            fn = call.get("function") or {}
            name = fn.get("name") or ""
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}

            result = execute_tool(
                name,
                args,
                access_token=access_token,
                session_context=session_context,
                user=user,
            )
            tool_trace.append({"tool": name, "arguments": args, "result": result})

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": serialize_tool_result(result),
                }
            )

    # Last resort: synthesize from tool results we already have
    if tool_trace:
        try:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Using ONLY the tool results above, answer the user's last message clearly. "
                        "Do not call any more tools."
                    ),
                }
            )
            data = _openai_chat(messages, tools=None)
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            reply = (message.get("content") or "").strip()
            if reply:
                return {
                    "reply": reply,
                    "tool_trace": tool_trace,
                    "session_context": session_context,
                    "step_limit_fallback": True,
                }
        except Exception:
            logger.exception("Miya step-limit synthesis fallback failed")

    return {
        "reply": (
            "I hit my step limit while working on that. "
            "Try a simpler request or ask me to continue one step at a time."
        ),
        "tool_trace": tool_trace,
        "session_context": session_context,
    }
