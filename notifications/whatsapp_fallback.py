"""Intelligent free-form WhatsApp messages when Meta templates are unavailable."""

from __future__ import annotations

import re
from typing import Any


# Meta error substrings / codes when a template is missing or not approved for locale.
_MISSING_TEMPLATE_MARKERS = (
    "132001",
    "template name does not exist",
    "does not exist in the translation",
    "template not found",
    "unknown template",
)


def is_missing_template_error(response_data: Any) -> bool:
    """True when Meta rejected the send because the template/locale is not available."""
    if not response_data:
        return False
    if isinstance(response_data, dict):
        if response_data.get("fallback") == "text":
            return False
        err = response_data.get("error")
        if isinstance(err, dict):
            blob = f"{err.get('message', '')} {err.get('code', '')}".lower()
            return any(m in blob for m in _MISSING_TEMPLATE_MARKERS)
        data = response_data.get("data")
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                blob = f"{err.get('message', '')} {err.get('code', '')}".lower()
                return any(m in blob for m in _MISSING_TEMPLATE_MARKERS)
        blob = str(response_data).lower()
        return any(m in blob for m in _MISSING_TEMPLATE_MARKERS)
    return any(m in str(response_data).lower() for m in _MISSING_TEMPLATE_MARKERS)


def body_param_texts(components: list[dict[str, Any]] | None) -> list[str]:
    """Extract {{n}} text values from template components (body + header)."""
    out: list[str] = []
    for block in components or []:
        if not isinstance(block, dict):
            continue
        for param in block.get("parameters") or []:
            if not isinstance(param, dict):
                continue
            if param.get("type") == "text":
                text = str(param.get("text") or "").strip()
                if text:
                    out.append(text)
    return out


def _pick(params: list[str], idx: int, ctx: dict[str, Any], *keys: str, default: str = "") -> str:
    if idx < len(params) and params[idx]:
        return params[idx]
    for k in keys:
        val = ctx.get(k)
        if val is not None and str(val).strip():
            return str(val).strip()
    return default


def _norm_template_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def _shift_assigned_text(params: list[str], ctx: dict[str, Any]) -> str:
    first = _pick(params, 0, ctx, "first_name", "staff_name", default="there")
    rest = _pick(params, 1, ctx, "restaurant_name", "restaurant", default="your workplace")
    date = _pick(params, 2, ctx, "shift_date", "date", default="")
    start = _pick(params, 3, ctx, "start_time", default="")
    end = _pick(params, 4, ctx, "end_time", default="")
    role = _pick(params, 5, ctx, "role", default="")
    dept = _pick(params, 6, ctx, "department", default="") if len(params) > 6 else ctx.get("department", "")
    title = _pick(params, 7, ctx, "shift_title", "title", default="") if len(params) > 7 else ctx.get("shift_title", "")
    location = _pick(params, 8, ctx, "workspace_location", "location", default="") if len(params) > 8 else ctx.get("location", "")
    notes = _pick(params, 9, ctx, "notes", "instructions", default="") if len(params) > 9 else ctx.get("notes", "")

    lines = [
        f"Hi {first}!",
        "",
        f"You've been scheduled at *{rest}*.",
    ]
    if title:
        lines.extend(["", f"🧾 *{title}*"])
    if date:
        lines.append(f"📆 {date}")
    if start and end:
        lines.append(f"⏰ {start} – {end}")
    elif start:
        lines.append(f"⏰ From {start}")
    if role:
        lines.append(f"👔 {role}")
    if dept:
        lines.append(f"🏷️ {dept}")
    if location:
        lines.append(f"📍 {location}")
    if notes and notes not in ("—", "-"):
        lines.append(f"📝 {notes}")
    lines.extend(
        [
            "",
            "When your shift starts, reply *Clock in* here and share your *live location*.",
            "Need help? Reply *Help* anytime.",
        ]
    )
    return "\n".join(lines)


def _clock_in_reminder_text(params: list[str], ctx: dict[str, Any]) -> str:
    first = _pick(params, 0, ctx, "first_name", default="there")
    start = _pick(params, 1, ctx, "start_time", default="")
    minutes = _pick(params, 2, ctx, "minutes_until", "minutes_from_now", default="")
    location = _pick(params, 3, ctx, "location", default="")
    shift_desc = _pick(params, 4, ctx, "shift_description", default="")
    duration = _pick(params, 5, ctx, "duration", default="")

    lines = [f"Hi {first}! ⏰", "", "Reminder — your shift is coming up."]
    if start:
        if minutes:
            lines.append(f"Starts at *{start}* ({minutes}).")
        else:
            lines.append(f"Starts at *{start}*.")
    if shift_desc:
        lines.append(f"Shift: {shift_desc}")
    if duration:
        lines.append(f"Duration: {duration}")
    if location:
        lines.append(f"📍 {location}")
    lines.extend(
        [
            "",
            "When you arrive, reply *Clock in* and share your *live location*.",
        ]
    )
    return "\n".join(lines)


def _welcome_text(params: list[str], ctx: dict[str, Any]) -> str:
    first = _pick(params, 0, ctx, "first_name", default="there")
    rest = _pick(params, 1, ctx, "restaurant_name", default="your workplace")
    return (
        f"Welcome, {first}! 🎉\n\n"
        f"Your staff account at *{rest}* is active.\n\n"
        "• Reply *Clock in* when you start a shift\n"
        "• Reply *Help* for commands\n"
        "• Tasks and checklists will arrive here from your manager"
    )


def _invite_text(params: list[str], ctx: dict[str, Any]) -> str:
    brand = _pick(params, 0, ctx, "brand", default="Mizan")
    first = _pick(params, 1, ctx, "first_name", default="there")
    link = _pick(params, 2, ctx, "invite_link", "link", default="")
    support = _pick(params, 3, ctx, "support_contact", default="")
    lines = [
        f"You're invited to join *{brand}*, {first}!",
        "",
        "Tap the link below to set up your account:",
    ]
    if link:
        lines.append(link)
    if support:
        lines.append(f"\nQuestions? {support}")
    return "\n".join(lines)


def _checklist_text(params: list[str], ctx: dict[str, Any]) -> str:
    question = _pick(params, 0, ctx, "question", "question_text", default="")
    if not question:
        question = str(ctx.get("message") or "Please complete this checklist step.")
    return f"*{question}*\n\nReply *Yes*, *No*, or *N/A*."


def _clock_in_success_text(params: list[str], ctx: dict[str, Any]) -> str:
    first = _pick(params, 0, ctx, "first_name", default="there")
    time = _pick(params, 1, ctx, "clock_in_time", "time", default="")
    rest = _pick(params, 2, ctx, "restaurant_name", default="")
    msg = f"You're clocked in, {first}!"
    if time:
        msg += f" ({time})"
    if rest:
        msg += f"\n\nHave a great shift at *{rest}*."
    msg += "\n\nReply *Start checklist* when you're ready, or *Help* if you need anything."
    return msg


def _announcement_text(params: list[str], ctx: dict[str, Any]) -> str:
    body = ctx.get("message") or ctx.get("body")
    if body:
        return str(body).strip()
    if params:
        return params[0]
    return "You have a new message from your team."


def _generic_text(params: list[str], ctx: dict[str, Any]) -> str:
    body = ctx.get("message") or ctx.get("body") or ctx.get("fallback_body")
    if body:
        return str(body).strip()
    if len(params) == 1:
        return params[0]
    if params:
        bullets = "\n".join(f"• {p}" for p in params[:8])
        return f"Update from your team:\n\n{bullets}"
    return ""


def compose_intelligent_fallback(
    template_name: str,
    *,
    components: list[dict[str, Any]] | None = None,
    fallback_body: str | None = None,
    context: dict[str, Any] | None = None,
) -> str:
    """
    Build a human, Miya-style free-form WhatsApp message from template metadata.
    Prefer explicit fallback_body/context.message when provided by the caller.
    """
    ctx = dict(context or {})
    if fallback_body and str(fallback_body).strip():
        return str(fallback_body).strip()

    params = body_param_texts(components)
    key = _norm_template_name(template_name)

    if any(k in key for k in ("weekly_schedule", "shift_assigned", "shift_assign", "schedule")):
        return _shift_assigned_text(params, ctx)
    if any(k in key for k in ("clock_in_reminder", "staff_clock_in", "clock_in")) and "success" not in key and "location" not in key:
        return _clock_in_reminder_text(params, ctx)
    if any(k in key for k in ("activated", "welcome")):
        return _welcome_text(params, ctx)
    if any(k in key for k in ("invite", "invitation", "onboarding")):
        return _invite_text(params, ctx)
    if "checklist" in key:
        return _checklist_text(params, ctx)
    if "success" in key and "clock" in key:
        return _clock_in_success_text(params, ctx)
    if any(k in key for k in ("announcement", "manager_message", "staff_message")):
        return _announcement_text(params, ctx)

    # Heuristic from parameter count / shape
    if len(params) >= 6 and params[2] and params[3] and params[4]:
        return _shift_assigned_text(params, ctx)
    if len(params) >= 4 and params[1] and ("clock" in key or "reminder" in key):
        return _clock_in_reminder_text(params, ctx)
    if len(params) >= 2 and not params[2]:
        return _welcome_text(params, ctx)

    text = _generic_text(params, ctx)
    if text:
        return text
    return (
        "Hi! Miya here — I have an update for you but couldn't use the usual WhatsApp template. "
        "Please open the Mizan app or reply *Help* and I'll assist you."
    )
