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
from miya.services.message_pipeline import (
    ExecutionStage,
    assert_user_initiated,
    attach_pipeline_meta,
    begin_turn,
    sanitize_history,
)
from .context import build_session_context, build_system_prompt
from .tools import execute_tool, serialize_tool_result, tools_for_user

logger = logging.getLogger(__name__)

MAX_TOOL_STEPS = 12


def _finalize_chat_result(
    *,
    turn,
    reply: str,
    session_context: dict[str, Any],
    tool_trace: list[dict[str, Any]] | None = None,
    language: str = "en",
    **extra: Any,
) -> dict[str, Any]:
    """Stamp FINAL_RESPONSE → END. Never re-enter the agent with this reply."""
    text = _finalize_reply(reply, language=language)
    body: dict[str, Any] = {
        "reply": text,
        "tool_trace": tool_trace or [],
        "session_context": session_context,
        **extra,
    }
    return attach_pipeline_meta(body, turn, text)

def _generic_fallback(lang: str = "en") -> str:
    from core.i18n import tr

    return tr("miya.wa.idle_prompt", lang)

_SCHEDULE_QUERY = re.compile(
    r"\b("
    r"who('s| is| are)?\s+(on\s+)?(schedule|scheduled|duty|working|shift)"
    r"|who\s+is\s+(on|working)"
    r"|on\s+schedule\s+(today|tonight|this)"
    r")\b",
    re.I,
)

_PENDING_OPS_QUERY = re.compile(
    r"(?:"
    r"(?:what|which|show|list|any|tell me|give me|do i have|where are we).{0,50}"
    r"(?:pending|open|en attente|new demand|operations live|tasks?|at today|today|status|stand)"
    r"|(?:pending|open)\s+tasks?"
    r"|tâches?\s+en\s+attente"
    r"|tasks?\s+(?:for|on)\s+today"
    r"|today'?s?\s+(?:open\s+)?tasks?"
    r"|what(?:'s|\s+is)\s+(?:on\s+)?operations\s+live"
    r"|where(?:'s|\s+are)\s+we\s+(?:at|with|on|today)"
    r"|status\s+update"
    r"|how(?:'s|\s+are)\s+(?:we|things|operations)"
    r"|morning\s+(?:brief|update|status|summary)"
    r"|(?:give me|need)\s+(?:a\s+)?(?:status|update|summary|briefing)"
    r"|on\s+ nous\s+en\s+est"
    r"|où\s+en\s+sommes"
    r")",
    re.I,
)

_MANAGER_ROLES = frozenset({"OWNER", "ADMIN", "SUPER_ADMIN", "MANAGER"})

# Manager → HR/payroll lane (not staff escalating their own wages).
_STAFF_SELF_PAY = re.compile(
    r"\b(my|mine|i have(n't| not)|where is my|missing my)\b.{0,40}\b("
    r"pay|salary|wages|payslip|payroll"
    r")\b|\b("
    r"pay|salary|wages|payslip|payroll"
    r")\b.{0,40}\b(my|mine)\b",
    re.I,
)

_PAYROLL_DELEGATION_HINTS = (
    "tell hr",
    "tell payroll",
    "tell human resources",
    "hr to pay",
    "payroll to",
    "ask hr",
    "ask payroll",
    "have hr",
    "pay all staff",
    "pay staff",
    "pay everyone",
    "pay all employees",
    "run payroll",
    "process payroll",
    "pay immediately",
    "pay now",
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
        tool = entry.get("tool")
        result = entry.get("result") or {}
        if tool == "list_operations_live":
            if not result.get("success", True):
                err = result.get("error") or result.get("message_for_user")
                if err:
                    return str(err)
                continue
            data = result.get("data") if isinstance(result.get("data"), dict) else result
            summary = (data or {}).get("message_for_user") or (data or {}).get("pending_summary")
            if summary:
                return str(summary).strip()
            continue
        if tool != "list_shifts":
            continue
        if not result.get("success", True):
            err = result.get("error") or result.get("message_for_user")
            if err:
                return str(err)
            continue
        shifts = _filter_shifts_for_query(_shifts_from_tool_result(result), user_message)
        area_hint = "the bar area" if "bar" in (user_message or "").lower() else ""
        return _format_shifts_reply(shifts, area_hint=area_hint)
    return None


def _looks_like_pending_ops_query(message: str, role: str) -> bool:
    """Board briefing only — never steal entity status questions."""
    from miya.services.ops.intent import looks_like_board_briefing

    return looks_like_board_briefing(message, role)


def _reply_from_operations_live_result(result: dict[str, Any]) -> str:
    data = result.get("data") if isinstance(result.get("data"), dict) else result
    summary = (data or {}).get("message_for_user") or (data or {}).get("pending_summary")
    if summary:
        return str(summary).strip()
    counts = (data or {}).get("counts") or {}
    n = counts.get("pending", 0)
    return f"No open items on Operations Live." if n == 0 else f"{n} new demand(s) on Operations Live."


def _try_manager_schedule_fast_path(
    *,
    user_message: str,
    session_context: dict[str, Any],
    user,
) -> dict[str, Any] | None:
    """Answer 'what's on my calendar / schedule / reminders' from live data."""
    import re

    role = (session_context.get("role") or getattr(user, "role", "") or "").upper()
    if role not in _MANAGER_ROLES:
        return None

    text = (user_message or "").strip().lower()
    if not text:
        return None

    # Strip attachment context prefix from WhatsApp enriched messages.
    if "user message:" in text:
        text = text.split("user message:", 1)[-1].strip()

    patterns = (
        r"\b(what'?s on my calendar|my calendar|my meetings|my schedule|my agenda)\b",
        r"\b(meetings|rendez[\s-]?vous|agenda|rappels?|reminders?)\s+(today|this week|demain|aujourd)",
        r"\b(calendar|calendrier)\s*\?",
        r"\bwhat do i have (today|this week|tomorrow)\b",
        r"\bqu'?est-ce que j'?ai (aujourd|demain|cette semaine)\b",
    )
    if not any(re.search(p, text) for p in patterns):
        return None

    from accounts.models import Restaurant
    from miya.services.manager_schedule_context import build_manager_schedule_block

    rid = session_context.get("restaurant_id")
    restaurant = Restaurant.objects.filter(id=rid).first() if rid else getattr(user, "restaurant", None)
    block = build_manager_schedule_block(user, restaurant)
    lang = session_context.get("language") or "en"

    if not block:
        reply = (
            "Je n'ai pas pu charger votre agenda pour le moment."
            if lang == "fr"
            else "I couldn't load your schedule right now — try again in a moment."
        )
    else:
        # Convert structured block into a concise chat reply.
        body = block.replace("[MANAGER SCHEDULE — calendar, reminders, today's shifts; authoritative for this manager]\n", "")
        body = body.replace(
            "Miya proactively pings this manager on WhatsApp before reminders and meetings.\n",
            "",
        ).strip()
        intro = (
            "Voici votre agenda :"
            if lang == "fr"
            else "Here's your schedule:"
        )
        reply = f"{intro}\n\n{body}\n\nJe vous préviens sur WhatsApp avant chaque rappel et rendez-vous."
        if lang != "fr":
            reply = f"{intro}\n\n{body}\n\nI'll ping you on WhatsApp before each reminder and meeting."

    return {
        "reply": _finalize_reply(reply, language=lang),
        "tool_trace": [{"tool": "manager_schedule_context", "arguments": {}, "result": {"success": True}}],
        "session_context": session_context,
        "provider": "django-fast-path",
    }


def _try_entity_status_fast_path(
    *,
    user_message: str,
    session_context: dict[str, Any],
    user,
    access_token: str | None,
) -> dict[str, Any] | None:
    """Retrieve live task/incident state — never invent status from board summary."""
    from miya.services.ops.intent import extract_status_query_subject, looks_like_entity_status

    role = (session_context.get("role") or getattr(user, "role", "") or "").upper()
    if role not in _MANAGER_ROLES:
        return None
    if not looks_like_entity_status(user_message):
        return None

    subject = extract_status_query_subject(user_message)
    args: dict[str, Any] = {}
    rid = session_context.get("restaurant_id")
    if rid:
        args["restaurant_id"] = str(rid)
    if subject:
        args["q"] = subject
        args["assignee_name"] = subject

    result = execute_tool(
        "get_dashboard_task" if subject else "find_tasks",
        args if subject else {**args, "status": "OPEN", "limit": 10},
        access_token=access_token,
        session_context=session_context,
        user=user,
    )
    lang = session_context.get("language") or "en"
    msg = (result or {}).get("message_for_user") or (result or {}).get("error")
    if not msg and (result or {}).get("success"):
        tasks = (result or {}).get("tasks") or []
        if tasks:
            t0 = tasks[0]
            msg = f"{t0.get('task_ref') or ''} {t0.get('title') or ''} is {t0.get('status')}."
    if not msg:
        return None
    return {
        "reply": _finalize_reply(str(msg), language=lang),
        "tool_trace": [{"tool": "get_dashboard_task", "arguments": args, "result": result}],
        "session_context": session_context,
        "provider": "django-fast-path",
    }


def _looks_like_operational_search(message: str) -> bool:
    """Phase 7 NL search — find/show/what happened/which staff…"""
    import re

    t = (message or "").strip()
    if not t or len(t) < 6:
        return False
    return bool(
        re.search(
            r"\b(find|show|search|look\s*up|retrieve|what\s+happened|who\s+handled|"
            r"which\s+staff|documents?\s+related|invoices?\s+from)\b",
            t,
            re.I,
        )
    )


def _looks_like_briefing_request(message: str) -> bool:
    """On-demand Daily Operations Intelligence (Phase 6)."""
    t = (message or "").strip().lower()
    if not t:
        return False
    needles = (
        "what needs my attention",
        "what needs attention",
        "daily briefing",
        "morning briefing",
        "ops briefing",
        "operational briefing",
        "where's the briefing",
        "where are we at",
        "where are we",
        "attention today",
        "what's on my plate",
        "whats on my plate",
    )
    return any(n in t for n in needles)


def _try_ambiguous_assign_fast_path(
    *,
    user_message: str,
    session_context: dict[str, Any],
    user,
) -> dict[str, Any] | None:
    """'Assign it to Ahmed' with no clear referent → ask, never guess."""
    from miya.services.ops.intent import looks_like_pronoun_assign
    from miya.services.working_set import get_entities, resolve_ids

    role = (session_context.get("role") or getattr(user, "role", "") or "").upper()
    if role not in _MANAGER_ROLES:
        return None
    if not looks_like_pronoun_assign(user_message):
        return None

    rid = str(session_context.get("restaurant_id") or "") or None
    uid = str(session_context.get("user_id") or getattr(user, "id", "") or "") or None
    resolved = resolve_ids(
        restaurant_id=rid,
        user_id=uid,
        kind="tasks",
        pronoun_hint="it",
    )
    lang = session_context.get("language") or "en"
    if len(resolved) == 1:
        return None  # Let the LLM / tools proceed with working-set resolution
    if len(resolved) > 1:
        labels = [
            str(ent.get("label"))
            for ent in get_entities(restaurant_id=rid, user_id=uid, kind="tasks")[:5]
            if isinstance(ent, dict) and ent.get("label")
        ]
        hint = (", ".join(labels) if labels else "the ones we just listed")
        reply = (
            f"Which task should I assign? I see a few candidates ({hint}). "
            "Give me the title or short ref — I won't guess."
        )
        return {
            "reply": _finalize_reply(reply, language=lang),
            "tool_trace": [{"tool": "clarify_assign_target", "arguments": {"candidates": resolved}, "result": {"success": False, "needs_clarification": True}}],
            "session_context": session_context,
            "provider": "django-fast-path",
        }

    reply = (
        "Which task should I assign? Tell me the title or short ref — "
        "I don't want to guess what 'it' refers to."
    )
    return {
        "reply": _finalize_reply(reply, language=lang),
        "tool_trace": [{"tool": "clarify_assign_target", "arguments": {}, "result": {"success": False, "needs_clarification": True}}],
        "session_context": session_context,
        "provider": "django-fast-path",
    }


def _try_pending_ops_fast_path(
    *,
    user_message: str,
    session_context: dict[str, Any],
    user,
    access_token: str | None,
) -> dict[str, Any] | None:
    role = (session_context.get("role") or getattr(user, "role", "") or "").upper()
    if not _looks_like_pending_ops_query(user_message, role):
        return None

    args = {"limit": 50}
    rid = session_context.get("restaurant_id")
    if rid:
        args["restaurant_id"] = str(rid)

    result = execute_tool(
        "list_operations_live",
        args,
        access_token=access_token,
        session_context=session_context,
        user=user,
    )
    lang = session_context.get("language") or "en"
    if not result.get("success", True):
        err = result.get("message_for_user") or result.get("error")
        if err:
            return {
                "reply": _finalize_reply(str(err), language=lang),
                "tool_trace": [{"tool": "list_operations_live", "arguments": args, "result": result}],
                "session_context": session_context,
                "provider": "django-fast-path",
            }
        return None

    return {
        "reply": _finalize_reply(_reply_from_operations_live_result(result), language=lang),
        "tool_trace": [{"tool": "list_operations_live", "arguments": args, "result": result}],
        "session_context": session_context,
        "provider": "django-fast-path",
    }


def _looks_like_manager_payroll_delegation(message: str, role: str) -> bool:
    text = (message or "").strip()
    if not text or (role or "").upper() not in _MANAGER_ROLES:
        return False
    if _STAFF_SELF_PAY.search(text):
        return False
    lower = text.lower()
    if not any(hint in lower for hint in _PAYROLL_DELEGATION_HINTS):
        return False
    from staff.intent_router import classify_request

    decision = classify_request(subject="", description=text)
    return (decision.category or "").upper() == "PAYROLL"


def _payroll_delegation_task_title(message: str) -> str:
    text = (message or "").strip()
    if not text:
        return "Run payroll"
    # Keep the card title short and action-oriented.
    for prefix in ("please ", "can you ", "could you ", "miya ", "hey miya ", "hi miya "):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :].strip()
    return text[:255] or "Run payroll"


def _reply_from_create_task_result(result: dict[str, Any]) -> str:
    if result.get("success"):
        data = result.get("data") if isinstance(result.get("data"), dict) else result
        msg = (data or {}).get("message_for_user") or result.get("message_for_user")
        if msg:
            return str(msg).strip()
        task = (data or {}).get("task") or {}
        title = task.get("title") or "the task"
        return f"Done — I created “{title}” on the Human Resources widget. You'll see it under open items (not “in progress” until someone starts it)."
    err = result.get("message_for_user") or result.get("error") or "I couldn't create that HR task."
    return str(err).strip()


def _try_payroll_delegation_fast_path(
    *,
    user_message: str,
    session_context: dict[str, Any],
    user,
    access_token: str | None,
) -> dict[str, Any] | None:
    role = (session_context.get("role") or getattr(user, "role", "") or "").upper()
    if not _looks_like_manager_payroll_delegation(user_message, role):
        return None

    title = _payroll_delegation_task_title(user_message)
    args = {
        "title": title,
        "description": user_message.strip(),
        "category": "PAYROLL",
        "assign_to_category": "PAYROLL",
        "priority": "URGENT",
        "user_message": user_message.strip(),
    }
    result = execute_tool(
        "create_dashboard_task",
        args,
        access_token=access_token,
        session_context=session_context,
        user=user,
    )
    lang = session_context.get("language") or "en"
    return {
        "reply": _finalize_reply(_reply_from_create_task_result(result), language=lang),
        "tool_trace": [{"tool": "create_dashboard_task", "arguments": args, "result": result}],
        "session_context": session_context,
        "provider": "django-fast-path",
    }


def _try_staff_delegation_fast_path(
    *,
    user_message: str,
    session_context: dict[str, Any],
    user,
    access_token: str | None,
) -> dict[str, Any] | None:
    role = (session_context.get("role") or getattr(user, "role", "") or "").upper()
    if role not in _MANAGER_ROLES:
        return None

    from miya.services.staff_delegation import parse_staff_delegation

    parsed = parse_staff_delegation(user_message)
    if not parsed:
        return None

    args = {
        "title": parsed["task_title"],
        "description": parsed["task_description"],
        "assignee_name": parsed["staff_name"],
        "source_text": user_message.strip(),
        "user_message": user_message.strip(),
        "notify_whatsapp": True,
    }
    result = execute_tool(
        "create_dashboard_task",
        args,
        access_token=access_token,
        session_context=session_context,
        user=user,
    )
    lang = session_context.get("language") or "en"
    return {
        "reply": _finalize_reply(_reply_from_create_task_result(result), language=lang),
        "tool_trace": [{"tool": "create_dashboard_task", "arguments": args, "result": result}],
        "session_context": session_context,
        "provider": "django-fast-path",
    }


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
    lang = session_context.get("language") or "en"
    if not result.get("success", True):
        err = result.get("error") or result.get("message_for_user")
        if err:
            return {
                "reply": _finalize_reply(str(err), language=lang),
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


def _finalize_reply(reply: str, *, language: str = "en") -> str:
    cleaned = format_miya_reply(reply)
    return cleaned or _generic_fallback(language)


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
    inbound_message_id: str | None = None,
) -> dict[str, Any]:
    """
    Run one Miya turn (may include multiple tool calls).

    Lifecycle:
      USER_MESSAGE → AGENT_REASONING → TOOL_CALL* → TOOL_RESULT* → FINAL_RESPONSE → END

    The natural-language ``reply`` is for the user only. It must NEVER be parsed
    or re-fed as a command to trigger another mutation.
    """
    from miya.services.intelligence.context_engine import build_execution_context
    from miya.services.intelligence.idempotency import claim_message_once
    from miya.services.intelligence.memory import MemoryStore, reality_overrides_memory

    exec_ctx = build_execution_context(
        user=user,
        channel=channel,
        session_hint=session_hint,
        preferred_restaurant_id=preferred_restaurant_id,
        inbound_message_id=inbound_message_id,
    )
    if not claim_message_once(exec_ctx.message_id):
        return {
            "reply": "I already processed that message.",
            "tool_trace": [],
            "session_context": exec_ctx.attach_to_session({}),
            "message_id": exec_ctx.message_id,
            "conversation_id": exec_ctx.conversation_id,
            "execution_stage": "END",
            "assistant_text_is_not_executable": True,
            "deduplicated_message": True,
        }

    session_context = build_session_context(
        user,
        channel=channel,
        preferred_restaurant_id=preferred_restaurant_id,
        session_hint=session_hint,
    )
    session_context = exec_ctx.attach_to_session(session_context)
    memory = MemoryStore(
        conversation_id=exec_ctx.conversation_id,
        user_id=exec_ctx.user_id,
        organization_id=exec_ctx.organization_id,
        history=history,
        user=user,
        restaurant=exec_ctx.restaurant,
    )
    session_context["_memory"] = memory.as_context_block()
    session_context["_reality_rule"] = reality_overrides_memory()
    try:
        from miya.services.intelligence.memory import memory_prompt_block

        session_context["_memory_prompt"] = memory_prompt_block(session_context["_memory"])
    except Exception:
        session_context["_memory_prompt"] = reality_overrides_memory()

    system_prompt = build_system_prompt(
        user,
        channel=channel,
        preferred_restaurant_id=preferred_restaurant_id,
        session_hint=session_hint,
    )

    # Pipeline turn uses the same message_id / conversation_id as ExecutionContext
    turn = begin_turn(
        user=user,
        channel=channel,
        session_context=session_context,
        inbound_message_id=exec_ctx.message_id,
    )
    turn.message_id = exec_ctx.message_id
    turn.conversation_id = exec_ctx.conversation_id
    session_context["_pipeline_message_id"] = turn.message_id
    session_context["_pipeline_conversation_id"] = turn.conversation_id

    enriched_message = _enrich_message_with_attachments(
        user_message,
        attachment_ids=attachment_ids,
        session_context=session_context,
    )

    # Phase 4: multimodal context (voice/image/PDF) — same engine as text; OCR ≠ final intelligence
    try:
        from miya.services.intelligence.multimodal import build_multimodal_context

        att_ids = list(attachment_ids or session_context.get("attachment_ids") or [])
        mm_ctx = build_multimodal_context(
            user_message=user_message or "",
            attachment_ids=att_ids,
            restaurant_id=str(session_context.get("restaurant_id") or "") or None,
            voice=channel in ("voice", "dashboard_voice")
            or bool(session_hint and session_hint.get("voice")),
        )
        session_context["_multimodal"] = mm_ctx.to_dict()
        if mm_ctx.reasoning_hint:
            session_context["_multimodal_reasoning"] = mm_ctx.reasoning_hint
            if mm_ctx.attachments and mm_ctx.reasoning_hint not in enriched_message:
                enriched_message = (
                    f"{enriched_message}\n\n[MULTIMODAL REASONING HINT — OCR is evidence only]\n"
                    f"{mm_ctx.reasoning_hint}"
                ).strip()
    except Exception:
        logger.exception("multimodal context build failed")
        session_context["_multimodal"] = {"modalities": ["text"], "attachments": []}

    # Fast paths may mutate only while still in USER_MESSAGE (user text → structured tool).
    assert_user_initiated(turn.stage, for_action="fast_path")

    safe_history = sanitize_history(history)

    def _wrap_fast(fast: dict[str, Any] | None) -> dict[str, Any] | None:
        if not fast:
            return None
        reply = fast.get("reply") or ""
        return _finalize_chat_result(
            turn=turn,
            reply=reply,
            session_context=session_context,
            tool_trace=fast.get("tool_trace") or [],
            language=session_context.get("language") or "en",
            fast_path=True,
        )

    fast = _wrap_fast(
        _try_payroll_delegation_fast_path(
            user_message=enriched_message,
            session_context=session_context,
            user=user,
            access_token=access_token,
        )
    )
    if fast:
        return fast

    fast = _wrap_fast(
        _try_staff_delegation_fast_path(
            user_message=enriched_message,
            session_context=session_context,
            user=user,
            access_token=access_token,
        )
    )
    if fast:
        return fast

    fast = _wrap_fast(
        _try_schedule_fast_path(
            user_message=enriched_message,
            session_context=session_context,
            user=user,
            access_token=access_token,
        )
    )
    if fast:
        return fast

    fast = _wrap_fast(
        _try_ambiguous_assign_fast_path(
            user_message=enriched_message,
            session_context=session_context,
            user=user,
        )
    )
    if fast:
        return fast

    # Phase 10: Operational Copilot — unified routing (Phases 3–9 integrated)
    try:
        from miya.services.intelligence.copilot import run_copilot_turn

        copilot = run_copilot_turn(
            user=user,
            user_message=user_message or "",
            enriched_message=enriched_message,
            session_context=session_context,
            restaurant=exec_ctx.restaurant,
            channel=channel,
            access_token=access_token,
            history=safe_history,
        )
        if copilot is not None:
            return _finalize_chat_result(
                turn=turn,
                reply=copilot.reply,
                session_context=session_context,
                tool_trace=copilot.tool_trace,
                language=session_context.get("language") or "en",
                **copilot.to_chat_extra(),
            )
    except Exception:
        logger.exception("operational copilot failed; falling through")

    fast = _wrap_fast(
        _try_entity_status_fast_path(
            user_message=enriched_message,
            session_context=session_context,
            user=user,
            access_token=access_token,
        )
    )
    if fast:
        return fast

    fast = _wrap_fast(
        _try_pending_ops_fast_path(
            user_message=enriched_message,
            session_context=session_context,
            user=user,
            access_token=access_token,
        )
    )
    if fast:
        return fast

    fast = _wrap_fast(
        _try_manager_schedule_fast_path(
            user_message=enriched_message,
            session_context=session_context,
            user=user,
        )
    )
    if fast:
        return fast

    turn.advance(ExecutionStage.AGENT_REASONING)

    from .mastra_client import mastra_enabled, run_miya_chat_mastra

    if mastra_enabled():
        try:
            result = run_miya_chat_mastra(
                user_message=enriched_message,
                history=safe_history,
                session_context=session_context,
                access_token=access_token,
                system_prompt=system_prompt,
            )
            return _finalize_chat_result(
                turn=turn,
                reply=result.get("reply") or "",
                session_context=result.get("session_context") or session_context,
                tool_trace=result.get("tool_trace") or [],
                language=session_context.get("language") or "en",
                provider=result.get("provider"),
            )
        except Exception as exc:
            logger.warning("Mastra provider failed, falling back to Django agent: %s", exc)

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for turn_row in safe_history:
        messages.append({"role": turn_row["role"], "content": turn_row["content"]})

    messages.append({"role": "user", "content": enriched_message.strip()})

    tool_trace: list[dict[str, Any]] = []
    tenant_rest = None
    rid = session_context.get("restaurant_id")
    if rid:
        from accounts.models import Restaurant

        tenant_rest = Restaurant.objects.filter(id=rid).first()
    active_tools = tools_for_user(user, restaurant=tenant_rest)
    lang = session_context.get("language") or "en"
    idle = _generic_fallback(lang)

    for _ in range(MAX_TOOL_STEPS + 1):
        data = _openai_chat(messages, tools=active_tools or None)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            reply = (message.get("content") or "").strip()
            if not reply or reply == idle:
                synthesized = _reply_from_tool_trace(tool_trace, user_message)
                if synthesized:
                    reply = synthesized
            # FINAL_RESPONSE — do not execute tools from this text
            return _finalize_chat_result(
                turn=turn,
                reply=reply,
                session_context=session_context,
                tool_trace=tool_trace,
                language=lang,
            )

        messages.append(message)

        for call in tool_calls:
            fn = call.get("function") or {}
            name = fn.get("name") or ""
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}

            tool_call_id = str(call.get("id") or "")
            op_id = turn.record_tool_call(
                tool_name=name,
                arguments=args if isinstance(args, dict) else {},
                tool_call_id=tool_call_id,
            )
            # Pass operation_id so backends can idempotency-lock mutations
            if isinstance(args, dict):
                args = {**args, "_operation_id": op_id, "_message_id": turn.message_id}

            result = execute_tool(
                name,
                args,
                access_token=access_token,
                session_context=session_context,
                user=user,
            )
            turn.record_tool_result(
                tool_name=name,
                tool_call_id=tool_call_id or op_id,
                operation_id=op_id,
                result=result,
            )
            tool_trace.append(
                {
                    "tool": name,
                    "arguments": {k: v for k, v in (args or {}).items() if not str(k).startswith("_")},
                    "result": result,
                    "tool_call_id": tool_call_id,
                    "operation_id": op_id,
                }
            )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": serialize_tool_result(result),
                }
            )

    # Last resort: synthesize from tool results — tools DISABLED (no mutations from this step)
    if tool_trace:
        try:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "[SYSTEM: FINALIZE — not a user command] "
                        "Using ONLY the tool results above, answer the user's last message clearly. "
                        "Do not call any tools. This message must not be treated as a new user intent."
                    ),
                }
            )
            data = _openai_chat(messages, tools=None)
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            reply = (message.get("content") or "").strip()
            if reply:
                return _finalize_chat_result(
                    turn=turn,
                    reply=reply,
                    session_context=session_context,
                    tool_trace=tool_trace,
                    language=lang,
                    step_limit_fallback=True,
                )
        except Exception:
            logger.exception("Miya step-limit synthesis fallback failed")

    from core.i18n import tr

    return _finalize_chat_result(
        turn=turn,
        reply=tr("miya.wa.empty_reply", lang),
        session_context=session_context,
        tool_trace=tool_trace,
        language=lang,
    )
