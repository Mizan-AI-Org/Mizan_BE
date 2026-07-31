"""HTTP client for the Mastra Miya agent service."""

from __future__ import annotations

import logging
from typing import Any

import requests
from django.conf import settings

from core.read_through_cache import get_or_set
from miya.services.reply_format import format_miya_reply
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
    headers = {
        "Content-Type": "application/json",
        # Mastra Cloud: only Django bridge calls require MIYA_MASTRA_API_KEY (Studio uses platform auth).
        "X-Miya-Django-Bridge": "1",
    }
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
    max_history = 8
    for turn in (history or [])[-max_history:]:
        role = turn.get("role")
        content = (turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_message.strip()})

    resource_id = str(session_context.get("user_id") or "anonymous")
    thread_id = _thread_id(session_context)

    max_steps = int(getattr(settings, "MIYA_MASTRA_MAX_STEPS", 8) or 8)

    payload: dict[str, Any] = {
        "messages": messages,
        "instructions": system_prompt[:8000],
        "maxSteps": max_steps,
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
                "systemPrompt": system_prompt[:8000],
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
        "reply": format_miya_reply(reply) or "I'm here. What would you like me to help with?",
        "tool_trace": tool_trace,
        "session_context": {**session_context, "thread_id": thread_id},
        "provider": "mastra",
    }


def _text_from_message_content(content: Any) -> str:
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        texts = [
            p.get("text", "")
            for p in content
            if isinstance(p, dict) and p.get("type") == "text" and p.get("text")
        ]
        joined = "\n".join(t for t in texts if t).strip()
        if joined:
            return joined
    return ""


def _extract_reply(data: dict[str, Any]) -> str:
    for key in ("text", "reply", "content"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    for bucket_key in ("response", "result", "data"):
        inner = data.get(bucket_key)
        if not isinstance(inner, dict):
            continue
        for key in ("text", "reply", "content"):
            val = inner.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        messages = inner.get("messages") or inner.get("uiMessages")
        if isinstance(messages, list):
            for msg in reversed(messages):
                if not isinstance(msg, dict):
                    continue
                if msg.get("role") not in (None, "assistant"):
                    continue
                text = _text_from_message_content(msg.get("content"))
                if text:
                    return text

    steps = data.get("steps")
    if isinstance(steps, list):
        for step in reversed(steps):
            if not isinstance(step, dict):
                continue
            for key in ("text", "response", "output"):
                val = step.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
                if isinstance(val, dict):
                    nested = _extract_reply(val)
                    if nested:
                        return nested

    fallback = _fallback_reply_from_tool_results(data)
    if fallback:
        return fallback
    return ""


def _fallback_reply_from_tool_results(data: dict[str, Any]) -> str:
    """When Mastra stops on tool-calls with empty text, surface tool/auth failures clearly."""
    tool_results = data.get("toolResults") or data.get("tool_results") or []
    if not isinstance(tool_results, list):
        return ""

    for entry in reversed(tool_results):
        if not isinstance(entry, dict):
            continue
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else entry
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        text = (result.get("text") or "").strip()
        if text:
            return text

        sub_results = result.get("subAgentToolResults") or result.get("toolResults") or []
        if isinstance(sub_results, list):
            for sub in reversed(sub_results):
                if not isinstance(sub, dict):
                    continue
                sub_payload = sub.get("payload") if isinstance(sub.get("payload"), dict) else sub
                inner = sub_payload.get("result")
                if isinstance(inner, dict):
                    data_block = inner.get("data") if isinstance(inner.get("data"), dict) else inner
                    if data_block.get("shifts"):
                        return _format_shifts_reply(data_block.get("shifts") or [])
                    err = (
                        inner.get("error")
                        or inner.get("message_for_user")
                        or (data_block.get("error") if isinstance(data_block, dict) else None)
                    )
                    if err:
                        return (
                            f"I couldn't reach Mizan tools ({err}). "
                            "If you're on localhost, set MIYA_AGENT_PROVIDER=django in .env "
                            "or redeploy production with MIYA_MASTRA_API_KEY."
                        )

    if tool_results:
        return (
            "I tried to look that up but didn't get a final answer from Miya. "
            "On localhost use MIYA_AGENT_PROVIDER=django; in production ensure MIYA_MASTRA_API_KEY is on the API server."
        )
    return ""


def _format_shifts_reply(shifts: list[dict[str, Any]]) -> str:
    if not shifts:
        return "No one is scheduled for that date."
    parts: list[str] = []
    for shift in shifts[:12]:
        if not isinstance(shift, dict):
            continue
        name = shift.get("staff_name") or shift.get("staff") or "Staff"
        start = shift.get("start_time") or shift.get("start") or ""
        end = shift.get("end_time") or shift.get("end") or ""
        area = shift.get("area") or shift.get("location") or shift.get("role") or ""
        slot = f"{start} to {end}".strip() if start or end else ""
        detail = ", ".join(p for p in (slot, str(area).strip()) if p)
        parts.append(f"{name} ({detail})" if detail else name)
    body = ". ".join(parts)
    if len(shifts) > 12:
        body += f". Plus {len(shifts) - 12} more."
    return format_miya_reply(f"Here's who's scheduled: {body}.")


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
