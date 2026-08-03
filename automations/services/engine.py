"""Execute tenant automations on WhatsApp / ops events."""

from __future__ import annotations

import copy
import logging
import re
from typing import Any

from django.utils import timezone

from automations.constants import ACTION_TYPES, QUICK_START_TEMPLATES, TEMPLATE_LIBRARY, TRIGGER_TYPES
from automations.models import AutomationRunLog, TenantAutomation

logger = logging.getLogger(__name__)

ACTION_ALIASES: dict[str, str] = {
    "send_reply": "send_message",
    "reply": "send_message",
    "send_text": "send_message",
    "whatsapp_reply": "send_message",
    "tag": "add_tag",
    "tag_contact": "add_tag",
    "add_contact_tag": "add_tag",
    "remove_contact_tag": "remove_tag",
    "create_follow_up_task": "create_task",
    "staff_request": "create_staff_request",
    "webhook": "send_webhook",
}

SALES_INTENT_WORDS = (
    "sales",
    "inquiry",
    "quote",
    "pricing",
    "price",
    "lead",
    "purchase",
    "order",
    "buy",
)


def _coerce_keywords(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [k.strip() for k in re.split(r"[,;\n]+", raw) if k.strip()]
    if isinstance(raw, (list, tuple)):
        out: list[str] = []
        for item in raw:
            out.extend(_coerce_keywords(item))
        return out
    return [str(raw).strip()] if str(raw).strip() else []


def normalize_automation_step(raw: Any) -> dict[str, Any]:
    """Coerce LLM / legacy step shapes into {type, config}."""
    if not isinstance(raw, dict):
        return {"type": "send_message", "config": {"text": str(raw or "").strip()}}

    stype = str(raw.get("type") or raw.get("action") or raw.get("step_type") or "").strip()
    stype = ACTION_ALIASES.get(stype.lower(), stype)

    cfg = dict(raw.get("config") or raw.get("parameters") or raw.get("params") or {})

    if raw.get("message"):
        cfg.setdefault("text", raw["message"])
    if raw.get("text") and not cfg.get("text"):
        cfg["text"] = raw["text"]
    if raw.get("template_name"):
        cfg.setdefault("template_name", raw["template_name"])
    if raw.get("tag"):
        if not stype or stype.lower() in {"add_tag", "remove_tag", "tag"}:
            stype = stype if stype in ACTION_TYPES else "add_tag"
        cfg.setdefault("tag", raw["tag"])
    if raw.get("url"):
        cfg.setdefault("url", raw["url"])
    if raw.get("title"):
        cfg.setdefault("title", raw["title"])
    if raw.get("subject"):
        cfg.setdefault("subject", raw["subject"])
    if raw.get("description") and stype in {"create_task", "create_staff_request"}:
        cfg.setdefault("description", raw["description"])
    if raw.get("category"):
        cfg.setdefault("category", raw["category"])
    if raw.get("priority"):
        cfg.setdefault("priority", raw["priority"])
    if raw.get("seconds") is not None:
        cfg.setdefault("seconds", raw["seconds"])
    if raw.get("staff_id"):
        cfg.setdefault("staff_id", raw["staff_id"])
    if raw.get("note"):
        cfg.setdefault("note", raw["note"])
    if raw.get("keywords") is not None and stype == "condition":
        cfg.setdefault("keywords", _coerce_keywords(raw["keywords"]))

    if not stype:
        if cfg.get("text"):
            stype = "send_message"
        elif cfg.get("tag"):
            stype = "add_tag"
        elif cfg.get("url"):
            stype = "send_webhook"
        elif cfg.get("title") or cfg.get("subject"):
            stype = "create_staff_request" if cfg.get("subject") else "create_task"
        else:
            stype = "send_message"

    stype = ACTION_ALIASES.get(stype.lower(), stype)
    if stype not in ACTION_TYPES:
        logger.warning("Unknown automation step type %r — defaulting to send_message", stype)
        if not cfg.get("text"):
            cfg["text"] = str(raw.get("message") or raw.get("text") or "")[:2000]
        stype = "send_message"

    return {"type": stype, "config": cfg}


def normalize_automation_steps(steps: Any) -> list[dict[str, Any]]:
    if not isinstance(steps, list):
        return []
    return [normalize_automation_step(step) for step in steps if step is not None]


def _intent_blob(data: dict[str, Any]) -> str:
    parts = [
        str(data.get("name") or ""),
        str(data.get("description") or ""),
        str(data.get("message") or ""),
    ]
    for step in data.get("steps") or []:
        if isinstance(step, dict):
            parts.append(str(step.get("message") or step.get("text") or ""))
    return " ".join(parts).lower()


def _infer_template_id(data: dict[str, Any]) -> str | None:
    if data.get("template_id"):
        return str(data["template_id"])
    blob = _intent_blob(data)
    if any(word in blob for word in SALES_INTENT_WORDS):
        return "sales_process"
    if "vip" in blob or "priority guest" in blob:
        return "keyword_vip"
    if "welcome" in blob or "first message" in blob:
        return "welcome_message"
    if "out of office" in blob or "off hours" in blob or "after hours" in blob:
        return "out_of_office"
    if "follow up" in blob or "follow-up" in blob:
        return "follow_up_reminder"
    return None


def _apply_miya_overrides(base: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    """Merge top-level Miya fields into a template-based automation."""
    result = copy.deepcopy(base)
    if data.get("name"):
        result["name"] = str(data["name"]).strip()
    if data.get("description"):
        result["description"] = str(data["description"]).strip()

    keywords = _coerce_keywords(data.get("keywords"))
    if keywords:
        result["trigger_type"] = "keyword_match"
        result["trigger_config"] = {"keywords": keywords}

    tag = str(data.get("tag") or "").strip()
    message = str(data.get("message") or "").strip()

    steps = normalize_automation_steps(result.get("steps") or [])
    if tag:
        has_tag = any(s["type"] == "add_tag" for s in steps)
        if has_tag:
            for step in steps:
                if step["type"] == "add_tag":
                    step["config"]["tag"] = tag
        else:
            steps.insert(0, {"type": "add_tag", "config": {"tag": tag}})
    if message:
        has_msg = any(s["type"] == "send_message" for s in steps)
        if has_msg:
            for step in steps:
                if step["type"] == "send_message":
                    step["config"]["text"] = message
                    break
        else:
            steps.insert(0, {"type": "send_message", "config": {"text": message}})

    if data.get("steps"):
        miya_steps = normalize_automation_steps(data["steps"])
        if len(miya_steps) < len(steps):
            covered = {s["type"] for s in miya_steps}
            for tpl_step in steps:
                if tpl_step["type"] not in covered:
                    miya_steps.append(copy.deepcopy(tpl_step))
        steps = miya_steps

    result["steps"] = steps
    result["is_active"] = bool(data.get("is_active", result.get("is_active", True)))
    result["stop_miya_on_match"] = bool(
        data.get("stop_miya_on_match", result.get("stop_miya_on_match", False))
    )
    return result


def _build_description_from_steps(steps: list[dict[str, Any]], trigger_type: str) -> str:
    parts: list[str] = []
    trigger_note = {
        "keyword_match": "Runs when an inbound message matches configured keywords.",
        "new_message_received": "Runs on every inbound WhatsApp message.",
        "first_message_from_contact": "Runs on the first message from a new contact.",
        "time_based": "Runs on a schedule or outside business hours.",
    }.get(trigger_type, "Runs when the trigger condition is met.")
    parts.append(trigger_note)
    for idx, step in enumerate(steps, start=1):
        stype = step.get("type") or ""
        cfg = step.get("config") or {}
        if stype == "send_message" and cfg.get("text"):
            preview = str(cfg["text"]).replace("\n", " ")[:80]
            parts.append(f"Step {idx}: reply with “{preview}”.")
        elif stype == "add_tag" and cfg.get("tag"):
            parts.append(f"Step {idx}: tag contact as {cfg['tag']}.")
        elif stype == "create_staff_request":
            parts.append(f"Step {idx}: open staff inbox request ({cfg.get('subject') or 'follow-up'}).")
        elif stype == "create_task":
            parts.append(f"Step {idx}: create dashboard task ({cfg.get('title') or 'follow-up'}).")
        else:
            label = ACTION_TYPES.get(stype, stype.replace("_", " "))
            parts.append(f"Step {idx}: {label}.")
    return " ".join(parts)


def summarize_automation_fields(fields: dict[str, Any]) -> str:
    """Human-readable summary for Miya to confirm what was saved."""
    lines = [f"Name: {fields.get('name')}"]
    trig = fields.get("trigger_type") or ""
    cfg = fields.get("trigger_config") or {}
    if trig == "keyword_match" and cfg.get("keywords"):
        lines.append(f"Trigger: keyword match ({', '.join(cfg['keywords'])})")
    else:
        lines.append(f"Trigger: {TRIGGER_TYPES.get(trig, trig)}")
    for idx, step in enumerate(fields.get("steps") or [], start=1):
        stype = step.get("type") or ""
        scfg = step.get("config") or {}
        if stype == "send_message":
            lines.append(f"  {idx}. Send message: {str(scfg.get('text') or '')[:120]}")
        elif stype == "add_tag":
            lines.append(f"  {idx}. Add tag: {scfg.get('tag')}")
        elif stype == "create_staff_request":
            lines.append(f"  {idx}. Staff request: {scfg.get('subject') or scfg.get('description') or 'inbox'}")
        else:
            lines.append(f"  {idx}. {stype}")
    return "\n".join(lines)


def _keywords_match(text: str, keywords: list[str]) -> bool:
    hay = (text or "").lower()
    for kw in keywords or []:
        if str(kw).strip().lower() in hay:
            return True
    return False


def _trigger_matches(
    automation: TenantAutomation,
    *,
    event: str,
    message_text: str,
    is_first_message: bool,
    session_context: dict[str, Any],
) -> bool:
    ttype = automation.trigger_type
    cfg = automation.trigger_config or {}

    if ttype == "new_message_received":
        return event == "message_received" and bool((message_text or "").strip())
    if ttype == "first_message_from_contact":
        return event == "message_received" and is_first_message
    if ttype == "keyword_match":
        return event == "message_received" and _keywords_match(
            message_text, cfg.get("keywords") or []
        )
    if ttype == "new_contact_created":
        return event == "contact_created"
    if ttype == "tag_added":
        wanted = (cfg.get("tag") or "").upper()
        tags = session_context.get("tags") or []
        return event == "tag_added" and wanted in {str(t).upper() for t in tags}
    if ttype == "time_based":
        # Evaluated by Celery sweep — not inline on message
        return False
    return False


def _execute_step(
    step: dict[str, Any],
    *,
    restaurant,
    user,
    phone_digits: str,
    session,
    message_text: str,
) -> dict[str, Any]:
    from notifications.services import notification_service

    stype = step.get("type") or ""
    cfg = step.get("config") or {}

    if stype == "send_message":
        text = (cfg.get("text") or "").strip()
        if text and phone_digits:
            notification_service.send_whatsapp_text(phone_digits, text)
        return {"action": stype, "sent": bool(text)}

    if stype == "add_tag":
        tag = str(cfg.get("tag") or "").strip().upper()
        if session and tag:
            ctx = dict(getattr(session, "context", None) or {})
            tags = list(ctx.get("tags") or [])
            if tag not in tags:
                tags.append(tag)
            ctx["tags"] = tags
            session.context = ctx
            session.save(update_fields=["context"])
        return {"action": stype, "tag": tag}

    if stype == "remove_tag":
        tag = str(cfg.get("tag") or "").strip().upper()
        if session and tag:
            ctx = dict(getattr(session, "context", None) or {})
            tags = [t for t in (ctx.get("tags") or []) if str(t).upper() != tag]
            ctx["tags"] = tags
            session.context = ctx
            session.save(update_fields=["context"])
        return {"action": stype, "tag": tag}

    if stype == "create_task":
        from dashboard.models import Task

        task = Task.objects.create(
            restaurant=restaurant,
            title=str(cfg.get("title") or "Automation follow-up")[:255],
            description=(cfg.get("description") or message_text or "")[:4000],
            priority=str(cfg.get("priority") or "MEDIUM").upper(),
            status="PENDING",
            source="SYSTEM",
            created_by=user if getattr(user, "pk", None) else None,
            assigned_to_id=cfg.get("assignee_id") or (user.id if user else None),
        )
        if cfg.get("notify_whatsapp", True) and task.assigned_to and task.assigned_to.phone:
            from notifications.services import notification_service

            notification_service.send_whatsapp_text(
                task.assigned_to.phone,
                f"New task from automation: {task.title}",
            )
        return {"action": stype, "task_id": str(task.id)}

    if stype == "create_staff_request":
        from staff.models import StaffRequest

        sr = StaffRequest.objects.create(
            restaurant=restaurant,
            staff=user,
            category=str(cfg.get("category") or "OPERATIONS").upper(),
            subject=str(cfg.get("subject") or "WhatsApp automation")[:255],
            description=(cfg.get("description") or message_text or "")[:4000],
            source="AUTOMATION",
            status="PENDING",
        )
        return {"action": stype, "request_id": str(sr.id)}

    if stype == "send_webhook":
        import requests

        url = (cfg.get("url") or "").strip()
        if not url:
            return {"action": stype, "skipped": True}
        try:
            r = requests.post(
                url,
                json={
                    "phone": phone_digits,
                    "message": message_text,
                    "restaurant_id": str(restaurant.id),
                },
                timeout=15,
            )
            return {"action": stype, "status_code": r.status_code}
        except Exception as exc:
            return {"action": stype, "error": str(exc)[:200]}

    if stype == "close_conversation":
        if session:
            session.state = "idle"
            session.save(update_fields=["state"])
        return {"action": stype}

    if stype == "wait":
        return {"action": stype, "seconds": cfg.get("seconds") or 0}

    if stype == "condition":
        branch = cfg.get("then") if _keywords_match(message_text, cfg.get("keywords") or []) else cfg.get("else")
        results = []
        if isinstance(branch, list):
            for sub in branch:
                results.append(
                    _execute_step(
                        sub,
                        restaurant=restaurant,
                        user=user,
                        phone_digits=phone_digits,
                        session=session,
                        message_text=message_text,
                    )
                )
        return {"action": stype, "results": results}

    return {"action": stype, "skipped": True, "reason": "unsupported"}


def run_automation(
    automation: TenantAutomation,
    *,
    event: str,
    phone_digits: str,
    user,
    session,
    message_text: str = "",
    is_first_message: bool = False,
) -> dict[str, Any]:
    session_ctx = dict(getattr(session, "context", None) or {}) if session else {}
    if not _trigger_matches(
        automation,
        event=event,
        message_text=message_text,
        is_first_message=is_first_message,
        session_context=session_ctx,
    ):
        return {"matched": False}

    step_results = []
    for step in normalize_automation_steps(automation.steps or []):
        step_results.append(
            _execute_step(
                step,
                restaurant=automation.restaurant,
                user=user,
                phone_digits=phone_digits,
                session=session,
                message_text=message_text,
            )
        )

    automation.run_count = (automation.run_count or 0) + 1
    automation.last_run_at = timezone.now()
    automation.save(update_fields=["run_count", "last_run_at", "updated_at"])

    AutomationRunLog.objects.create(
        automation=automation,
        restaurant=automation.restaurant,
        phone=phone_digits or "",
        trigger_event=event,
        success=True,
        detail={"steps": step_results},
    )

    return {
        "matched": True,
        "automation_id": str(automation.id),
        "stop_miya": automation.stop_miya_on_match,
        "steps": step_results,
    }


def run_automations_for_whatsapp_message(
    *,
    restaurant,
    phone_digits: str,
    user,
    session,
    message_text: str,
    is_first_message: bool = False,
) -> dict[str, Any]:
    """Run all active automations for a tenant; return aggregate result."""
    if not restaurant:
        return {"ran": 0, "stop_miya": False}

    qs = TenantAutomation.objects.filter(
        restaurant=restaurant, is_active=True
    ).exclude(trigger_type="time_based")

    ran = 0
    stop_miya = False
    hits: list[dict] = []
    for auto in qs:
        try:
            result = run_automation(
                auto,
                event="message_received",
                phone_digits=phone_digits,
                user=user,
                session=session,
                message_text=message_text,
                is_first_message=is_first_message,
            )
            if result.get("matched"):
                ran += 1
                hits.append(result)
                if result.get("stop_miya"):
                    stop_miya = True
        except Exception:
            logger.exception("Automation %s failed", auto.id)

    return {"ran": ran, "stop_miya": stop_miya, "hits": hits}


def build_automation_from_template(template_id: str, *, name: str | None = None) -> dict:
    for tpl in TEMPLATE_LIBRARY:
        if tpl["id"] == template_id:
            meta = next((t for t in QUICK_START_TEMPLATES if t["id"] == template_id), {})
            return {
                "name": name or meta.get("name") or tpl["id"].replace("_", " ").title(),
                "description": meta.get("description") or "",
                "template_id": tpl["id"],
                "trigger_type": tpl["trigger"]["type"],
                "trigger_config": tpl["trigger"].get("config") or {},
                "steps": tpl.get("steps") or [],
            }
    raise ValueError(f"Unknown template: {template_id}")


def build_automation_from_miya_payload(data: dict[str, Any]) -> dict:
    """Normalize Miya / agent create payload into automation fields."""
    template_id = _infer_template_id(data)
    if template_id:
        base = build_automation_from_template(template_id, name=data.get("name"))
        fields = _apply_miya_overrides(base, data)
    else:
        trigger_type = data.get("trigger_type") or "new_message_received"
        trigger_config = dict(data.get("trigger_config") or {})
        keywords = _coerce_keywords(data.get("keywords"))
        if keywords and not trigger_config.get("keywords"):
            trigger_config["keywords"] = keywords
            if trigger_type == "new_message_received":
                trigger_type = "keyword_match"

        steps = normalize_automation_steps(data.get("steps") or [])
        if not steps and data.get("message"):
            steps.append({"type": "send_message", "config": {"text": str(data["message"]).strip()}})
        tag = str(data.get("tag") or "").strip()
        if tag and not any(s.get("type") == "add_tag" for s in steps):
            steps.insert(0, {"type": "add_tag", "config": {"tag": tag}})
        if not steps and data.get("action") == "add_tag" and tag:
            steps = [{"type": "add_tag", "config": {"tag": tag}}]

        fields = {
            "name": str(data.get("name") or "Miya automation").strip(),
            "description": str(data.get("description") or "").strip(),
            "trigger_type": trigger_type,
            "trigger_config": trigger_config,
            "steps": steps,
            "template_id": "",
            "is_active": bool(data.get("is_active", True)),
            "stop_miya_on_match": bool(data.get("stop_miya_on_match", False)),
        }

    if not fields.get("description") and fields.get("steps"):
        fields["description"] = _build_description_from_steps(
            fields["steps"],
            fields.get("trigger_type") or "",
        )

    fields["steps"] = normalize_automation_steps(fields.get("steps") or [])
    if not fields["steps"]:
        raise ValueError(
            "Automation needs at least one action step (send_message, add_tag, create_task, etc.)."
        )
    return fields
