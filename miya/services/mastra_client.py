"""HTTP client for the Mastra Miya agent service."""

from __future__ import annotations

import logging
from typing import Any

import requests
from django.conf import settings

from core.read_through_cache import get_or_set
from miya.cache_keys import mastra_health_key

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


def _probe_mastra_health() -> dict[str, Any]:
    url = f"{_base_url()}/api/agents"
    headers = _auth_headers()
    timeout = min(int(getattr(settings, "MIYA_MASTRA_TIMEOUT", 120) or 120), 10)
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        return {"ok": False, "reason": str(exc)[:200]}
    if resp.status_code >= 400:
        return {"ok": False, "reason": f"HTTP {resp.status_code}"}
    data = resp.json() if resp.content else {}
    agent_ids: list[str] = []
    if isinstance(data, dict):
        if "agents" in data and isinstance(data["agents"], list):
            items = data["agents"]
        else:
            items = list(data.keys())
        for item in items:
            if isinstance(item, dict):
                agent_ids.append(str(item.get("id") or item.get("name") or ""))
            elif isinstance(item, str):
                agent_ids.append(item)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                agent_ids.append(str(item.get("id") or item.get("name") or ""))
            elif isinstance(item, str):
                agent_ids.append(item)
    agent_ids = [a for a in agent_ids if a]
    target = _agent_id()
    return {
        "ok": True,
        "agent_id": target,
        "agents": agent_ids,
        "agent_registered": target in agent_ids or not agent_ids,
    }


def mastra_health() -> dict[str, Any]:
    """Probe Mastra server availability (agents list)."""
    if not mastra_enabled():
        return {"ok": False, "reason": "provider_not_mastra"}
    ttl = int(getattr(settings, "MIYA_MASTRA_HEALTH_CACHE_TTL", 30) or 30)
    return get_or_set(mastra_health_key(), ttl, _probe_mastra_health)


def _auth_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    for setting_name in ("MIYA_MASTRA_API_KEY", "MIYA_MASTRA_API_TOKEN", "MASTRA_API_TOKEN"):
        token = (getattr(settings, setting_name, None) or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
            return headers
    return headers


def mastra_deployment_mode() -> str:
    url = (_base_url() or "").lower()
    if not url:
        return "disabled"
    if "localhost" in url or "127.0.0.1" in url:
        return "local"
    return "cloud"


def _thread_id(session_context: dict[str, Any]) -> str:
    explicit = session_context.get("thread_id")
    if explicit:
        return str(explicit)
    channel = (session_context.get("channel") or "dashboard").strip().lower()
    user_id = session_context.get("user_id") or "anonymous"
    restaurant_id = session_context.get("restaurant_id") or "none"
    return f"mizan-{channel}-{user_id}-{restaurant_id}"


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

    # Mastra manages thread memory; still send recent turns for continuity on first call.
    for turn in history or []:
        role = turn.get("role")
        content = (turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_message.strip()})

    resource_id = str(session_context.get("user_id") or "anonymous")
    thread_id = _thread_id(session_context)

    payload: dict[str, Any] = {
        "messages": messages,
        "instructions": system_prompt[:12000],
        "maxSteps": 16,
        "memory": {
            "thread": thread_id,
            "resource": resource_id,
        },
        "requestContext": {
            "mizan": {
                "restaurantId": session_context.get("restaurant_id"),
                "userId": session_context.get("user_id"),
                "accessToken": access_token,
                "channel": session_context.get("channel") or "dashboard",
                "role": session_context.get("role"),
                "systemPrompt": system_prompt[:12000],
            },
        },
    }

    timeout = int(getattr(settings, "MIYA_MASTRA_TIMEOUT", 120) or 120)

    try:
        resp = requests.post(url, json=payload, headers=_auth_headers(), timeout=timeout)
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
        "session_context": {**session_context, "thread_id": thread_id},
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
    trace = data.get("tool_trace") or data.get("toolTrace") or data.get("toolCalls")
    if isinstance(trace, list):
        return [{"tool": t.get("name") or t.get("toolName"), "result": t} for t in trace if isinstance(t, dict)]
    inner = data.get("response") or data.get("result")
    if isinstance(inner, dict):
        trace = inner.get("toolCalls") or inner.get("tool_trace")
        if isinstance(trace, list):
            return [{"tool": t.get("name") or t.get("toolName"), "result": t} for t in trace if isinstance(t, dict)]
    return []
