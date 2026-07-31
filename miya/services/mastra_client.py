"""HTTP client for the Mastra Miya agent service."""

from __future__ import annotations

import logging
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def mastra_enabled() -> bool:
    provider = (getattr(settings, "MIYA_AGENT_PROVIDER", None) or "django").strip().lower()
    if provider != "mastra":
        return False
    return bool((getattr(settings, "MIYA_MASTRA_URL", None) or "").strip())


def _base_url() -> str:
    return (getattr(settings, "MIYA_MASTRA_URL", None) or "").rstrip("/")


def _agent_id() -> str:
    return (getattr(settings, "MIYA_MASTRA_AGENT_ID", None) or "miya").strip()


def run_miya_chat_mastra(
    *,
    user_message: str,
    history: list[dict[str, str]] | None,
    session_context: dict[str, Any],
    access_token: str | None,
    system_prompt: str,
) -> dict[str, Any]:
    """
    Call Mastra agent generate API. Returns {reply, tool_trace?, session_context}.
    """
    url = f"{_base_url()}/api/agents/{_agent_id()}/generate"
    messages: list[dict[str, str]] = []

    # Mastra manages its own memory thread; send recent history for continuity.
    for turn in history or []:
        role = turn.get("role")
        content = (turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_message.strip()})

    payload: dict[str, Any] = {
        "messages": messages,
        "resourceId": str(session_context.get("user_id") or "anonymous"),
        "threadId": session_context.get("thread_id")
        or f"mizan-{session_context.get('user_id')}-{session_context.get('restaurant_id')}",
        "runtimeContext": {
            "mizan": {
                "restaurantId": session_context.get("restaurant_id"),
                "userId": session_context.get("user_id"),
                "accessToken": access_token,
                "channel": session_context.get("channel") or "dashboard",
                "role": session_context.get("role"),
                "systemPrompt": system_prompt[:8000],
            },
        },
    }

    headers = {"Content-Type": "application/json"}
    mastra_key = (getattr(settings, "MIYA_MASTRA_API_KEY", None) or "").strip()
    if mastra_key:
        headers["Authorization"] = f"Bearer {mastra_key}"

    timeout = int(getattr(settings, "MIYA_MASTRA_TIMEOUT", 120) or 120)

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        logger.exception("Mastra request failed: %s", exc)
        raise RuntimeError("Miya Mastra service unreachable") from exc

    if resp.status_code >= 400:
        logger.warning("Mastra error %s: %s", resp.status_code, resp.text[:500])
        raise RuntimeError(f"Mastra error {resp.status_code}")

    data = resp.json() if resp.content else {}

    reply = _extract_reply(data)
    tool_trace = _extract_tool_trace(data)

    return {
        "reply": reply or "I'm here. What would you like me to help with?",
        "tool_trace": tool_trace,
        "session_context": session_context,
        "provider": "mastra",
    }


def _extract_reply(data: dict[str, Any]) -> str:
    for key in ("text", "reply", "content"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    inner = data.get("response") or data.get("result") or data.get("data")
    if isinstance(inner, dict):
        for key in ("text", "reply", "content"):
            val = inner.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        messages = inner.get("messages") or inner.get("uiMessages")
        if isinstance(messages, list) and messages:
            last = messages[-1]
            if isinstance(last, dict):
                parts = last.get("content")
                if isinstance(parts, str):
                    return parts.strip()
                if isinstance(parts, list):
                    texts = [
                        p.get("text", "")
                        for p in parts
                        if isinstance(p, dict) and p.get("type") == "text"
                    ]
                    joined = "\n".join(t for t in texts if t).strip()
                    if joined:
                        return joined
    return ""


def _extract_tool_trace(data: dict[str, Any]) -> list[dict[str, Any]]:
    trace = data.get("tool_trace") or data.get("toolTrace")
    if isinstance(trace, list):
        return trace
    inner = data.get("response") or data.get("result")
    if isinstance(inner, dict):
        trace = inner.get("toolCalls") or inner.get("tool_trace")
        if isinstance(trace, list):
            return [{"tool": t.get("name"), "result": t} for t in trace if isinstance(t, dict)]
    return []
