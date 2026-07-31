"""Miya conversational agent — OpenAI reasoning + Mizan tools + Fish Audio voice."""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from typing import Any

import requests
from django.conf import settings

from miya.services.reply_format import format_miya_reply
from .context import build_session_context, build_system_prompt
from .tools import execute_tool, serialize_tool_result, tools_for_user

logger = logging.getLogger(__name__)

MAX_TOOL_STEPS = 12
_GENERIC_FALLBACK = "I'm here. What would you like me to help with?"

_SCHEDULE_QUERY = re.compile(
    r"\b("
    r"who('s| is| are)?\s+(on\s+)?(schedule|scheduled|duty|working|shift)"
    r"|who\s+is\s+(on|working)"
    r"|on\s+schedule\s+(today|tonight|this)"
    r")\b",
    re.I,
)


def _looks_like_schedule_query(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    if _SCHEDULE_QUERY.search(text):
        return True
    lower = text.lower()
    return "schedule" in lower and "who" in lower


def _shift_area_tokens(shift: dict[str, Any]) -> str:
    parts = [
        shift.get("staff_name"),
        shift.get("role"),
        shift.get("position"),
        shift.get("area"),
        shift.get("location"),
        shift.get("shift_description"),
        shift.get("description"),
    ]
    return " ".join(str(p) for p in parts if p).lower()


def _filter_shifts_for_query(shifts: list[dict[str, Any]], message: str) -> list[dict[str, Any]]:
    lower = (message or "").lower()
    area_terms = []
    if "bar" in lower:
        area_terms.append("bar")
    if "kitchen" in lower:
        area_terms.append("kitchen")
    if "floor" in lower:
        area_terms.append("floor")
    if not area_terms:
        return shifts
    filtered = [s for s in shifts if any(t in _shift_area_tokens(s) for t in area_terms)]
    return filtered or shifts


def _format_shift_time(value: Any) -> str:
    if not value:
        return ""
    raw = str(value)
    try:
        if "T" in raw:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt.strftime("%H:%M")
    except Exception:
        pass
    return raw[:5] if len(raw) >= 5 else raw


def _format_shifts_reply(shifts: list[dict[str, Any]], *, area_hint: str = "") -> str:
    if not shifts:
        hint = f" for {area_hint}" if area_hint else ""
        return f"No one is scheduled{hint} today."
    parts: list[str] = []
    for shift in shifts[:12]:
        name = shift.get("staff_name") or shift.get("staff") or "Staff"
        start = _format_shift_time(shift.get("start_time") or shift.get("start"))
        end = _format_shift_time(shift.get("end_time") or shift.get("end"))
        slot = f"{start} to {end}".strip() if start or end else ""
        area = shift.get("area") or shift.get("role") or shift.get("shift_description") or ""
        detail = ", ".join(p for p in (slot, str(area).strip()) if p)
        parts.append(f"{name} ({detail})" if detail else name)
    intro = "Here's who's on schedule today:"
    body = ". ".join(parts)
    if len(shifts) > 12:
        body += f". Plus {len(shifts) - 12} more."
    return format_miya_reply(f"{intro} {body}.")


def _shifts_from_tool_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    data = result.get("data") if isinstance(result.get("data"), dict) else result
    shifts = (data or {}).get("shifts") if isinstance(data, dict) else None
    return shifts if isinstance(shifts, list) else []


def _reply_from_tool_trace(tool_trace: list[dict[str, Any]], user_message: str) -> str | None:
    for entry in reversed(tool_trace):
        if entry.get("tool") != "list_shifts":
            continue
        result = entry.get("result") or {}
        if not result.get("success", True):
            err = result.get("error") or result.get("message_for_user")
            if err:
                return str(err)
            continue
        shifts = _filter_shifts_for_query(_shifts_from_tool_result(result), user_message)
        area_hint = "the bar area" if "bar" in (user_message or "").lower() else ""
        return _format_shifts_reply(shifts, area_hint=area_hint)
    return None


def _try_schedule_fast_path(
    *,
    user_message: str,
    session_context: dict[str, Any],
    user,
    access_token: str | None,
) -> dict[str, Any] | None:
    if not _looks_like_schedule_query(user_message):
        return None

    today = (session_context.get("local_time") or "")[:10] or date.today().isoformat()
    result = execute_tool(
        "list_shifts",
        {"date": today},
        access_token=access_token,
        session_context=session_context,
        user=user,
    )
    if not result.get("success", True):
        err = result.get("error") or result.get("message_for_user")
        if err:
            return {
                "reply": _finalize_reply(str(err)),
                "tool_trace": [{"tool": "list_shifts", "arguments": {"date": today}, "result": result}],
                "session_context": session_context,
                "provider": "django-fast-path",
            }
        return None

    shifts = _filter_shifts_for_query(_shifts_from_tool_result(result), user_message)
    area_hint = "the bar area" if "bar" in user_message.lower() else ""
    return {
        "reply": _format_shifts_reply(shifts, area_hint=area_hint),
        "tool_trace": [{"tool": "list_shifts", "arguments": {"date": today}, "result": result}],
        "session_context": session_context,
        "provider": "django-fast-path",
    }


def _openai_model_name() -> str:
    """OpenAI Chat Completions expects bare model ids (not Mastra-style ``openai/`` prefixes)."""
    raw = (getattr(settings, "MIYA_CHAT_MODEL", None) or "gpt-4o-mini").strip()
    if raw.startswith("openai/"):
        return raw.split("/", 1)[1]
    return raw


def _openai_chat(messages: list[dict[str, Any]], *, tools: list | None = None) -> dict[str, Any]:
    api_key = getattr(settings, "OPENAI_API_KEY", "") or ""
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    model = _openai_model_name()
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


def _finalize_reply(reply: str) -> str:
    cleaned = format_miya_reply(reply)
    return cleaned or _GENERIC_FALLBACK


def _enrich_message_with_attachments(
    user_message: str,
    *,
    attachment_ids: list[str] | None,
    session_context: dict[str, Any],
) -> str:
    ids = list(attachment_ids or session_context.get("attachment_ids") or [])
    if not ids:
        return user_message
    rid = session_context.get("restaurant_id")
    if not rid:
        return user_message
    from miya.services.tenant_documents import attachment_context_block, documents_for_ids

    docs = documents_for_ids(rid, [str(i) for i in ids if i])
    block = attachment_context_block(docs)
    if not block:
        return user_message
    return f"{block}\nUser message: {(user_message or '').strip()}".strip()


def run_miya_chat(
    *,
    user,
    access_token: str | None,
    user_message: str,
    history: list[dict[str, str]] | None = None,
    channel: str = "dashboard",
    preferred_restaurant_id: str | None = None,
    session_hint: dict[str, Any] | None = None,
    attachment_ids: list[str] | None = None,
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

    enriched_message = _enrich_message_with_attachments(
        user_message,
        attachment_ids=attachment_ids,
        session_context=session_context,
    )

    fast = _try_schedule_fast_path(
        user_message=enriched_message,
        session_context=session_context,
        user=user,
        access_token=access_token,
    )
    if fast:
        return fast

    from .mastra_client import mastra_enabled, run_miya_chat_mastra

    if mastra_enabled():
        try:
            return run_miya_chat_mastra(
                user_message=enriched_message,
                history=history,
                session_context=session_context,
                access_token=access_token,
                system_prompt=system_prompt,
            )
        except Exception as exc:
            logger.warning("Mastra provider failed, falling back to Django agent: %s", exc)

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for turn in history or []:
        role = turn.get("role")
        content = (turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": enriched_message.strip()})

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
            if not reply or reply == _GENERIC_FALLBACK:
                synthesized = _reply_from_tool_trace(tool_trace, user_message)
                if synthesized:
                    reply = synthesized
            return {
                "reply": _finalize_reply(reply),
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
                    "reply": _finalize_reply(reply),
                    "tool_trace": tool_trace,
                    "session_context": session_context,
                    "step_limit_fallback": True,
                }
        except Exception:
            logger.exception("Miya step-limit synthesis fallback failed")

    return {
        "reply": _finalize_reply(
            "I hit my step limit while working on that. "
            "Try a simpler request or ask me to continue one step at a time."
        ),
        "tool_trace": tool_trace,
        "session_context": session_context,
    }
