"""WhatsApp lifecycle for ``dashboard.Task`` — thin adapter over unified experience.

Staff keyword replies call the same ``execute_structured_action`` → ops path
used by Dashboard, Mobile, Voice, and Miya. No WhatsApp-specific business logic.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def looks_like_dashboard_task_status_reply(text: str | None) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    tl = t.lower()
    # Checklist owns these — not dashboard.Task status updates.
    if re.match(
        r"^(start|begin|run|do)\s+(my\s+)?(the\s+)?(task\s+)?checklist\b",
        tl,
    ):
        return False
    if re.match(r"^(stick to|stay on|continue|resume|back to)\s+(the\s+)?checklist\b", tl):
        return False
    if tl in ("yes", "y", "no", "n/a", "na", "n a"):
        return False
    if re.match(
        r"^(done|complete|completed|finish|finished|accept|accepted|"
        r"start|started|progress|unable|can'?t|cannot|reject|rejected|"
        r"cancel|cancelled|my tasks|tasks|list tasks|"
        r"terminer|accepté|commencer|incapable|"
        r"إتمام|قبول|بدء|تعذر)\b",
        tl,
    ):
        return True
    if re.match(r"^(done|complete|accept|start|unable)\s+#?\d", tl):
        return True
    # Natural language: "I completed my closing checklist"
    if re.search(r"\b(i\s+)?(have\s+)?(completed?|finished|done)\b", tl) and re.search(
        r"\b(task|checklist|demande|closing|my)\b", tl
    ):
        return True
    if re.search(r"\b(mark|set|change)\b.+\b(completed?|done|finished|in\s+progress)\b", tl):
        return True
    return False


def _normalize_status_intent(text: str) -> str | None:
    t = (text or "").strip().lower()
    if re.match(
        r"^(start|begin|run|do)\s+(my\s+)?(the\s+)?(task\s+)?checklist\b",
        t,
    ):
        return None
    if re.search(r"\b(my tasks|tasks|list tasks)\b", t) and not re.search(
        r"\b(done|complete|accept|start|unable)\b", t
    ):
        return "LIST"
    if re.search(r"\b(unable|can'?t complete|cannot complete|incapable|تعذر)\b", t):
        return "UNABLE_TO_COMPLETE"
    if re.search(r"\b(reject|rejected|cancel|cancelled)\b", t):
        return "CANCELLED"
    if re.search(r"\b(done|complete|completed|finish|finished|terminer|إتمام)\b", t):
        return "COMPLETED"
    if re.search(r"\b(accept|accepted|قبول|accepté)\b", t):
        return "ACCEPTED"
    if re.search(r"\b(start|started|progress|in progress|commencer|بدء)\b", t):
        return "IN_PROGRESS"
    return None


def _extract_task_query(text: str) -> str:
    """Pull title fragment / short ref from natural language."""
    t = (text or "").strip()
    m = re.search(r"#([0-9a-fA-F]{6,8})\b", t)
    if m:
        return m.group(1)
    # "I completed my closing checklist" → closing checklist
    m = re.search(
        r"(?:completed?|finished|done|accepted?|started?)\s+(?:my\s+|the\s+)?(.+?)(?:\s+task)?\s*[.!]?\s*$",
        t,
        re.I,
    )
    if m:
        frag = m.group(1).strip(" .!")
        if frag.lower() not in ("it", "that", "this", "my task", "the task"):
            return frag
    return ""


def _ops_ctx(user):
    from miya.services.intelligence.unified import ops_context_for_channel

    return ops_context_for_channel(user=user, channel="whatsapp")


def complete_task_after_proof(
    *,
    user,
    task_id: str,
    notify_managers: bool = True,
) -> dict[str, Any]:
    """Called after photo proof is saved — unified COMPLETED path (events + memory)."""
    from miya.services.intelligence.unified import apply_task_status

    result = apply_task_status(
        user=user,
        channel="whatsapp",
        status="COMPLETED",
        task_id=str(task_id),
        assignee_scope=True,
        notify_managers=notify_managers,
        message_id=f"whatsapp:proof:{task_id}",
    )
    return result.as_tool_response()


def handle_dashboard_task_whatsapp_reply(
    *,
    notification_service,
    user,
    phone_digits: str,
    text_body: str,
    session=None,
) -> bool:
    """
    Returns True if the message was handled (caller should ``continue``).
    Mutates tasks only via unified ``apply_task_status`` (execute_structured_action).
    """
    if not user or not text_body:
        return False
    if not looks_like_dashboard_task_status_reply(text_body):
        return False
    intent = _normalize_status_intent(text_body)
    if not intent:
        return False

    ctx = _ops_ctx(user)
    if ctx is None:
        notification_service.send_whatsapp_text(
            phone_digits,
            "I couldn't determine your workspace. Ask your manager to link your account.",
        )
        return True

    from miya.services.intelligence.unified import apply_task_status
    from miya.services.ops.tasks import find_tasks

    if intent == "LIST":
        result = find_tasks(ctx, mine_only=True, status="OPEN", limit=15)
        tasks = (result.data or {}).get("tasks") or []
        if not result.success or not tasks:
            notification_service.send_whatsapp_text(
                phone_digits,
                "No open tasks assigned to you right now.",
            )
            return True
        lines = ["*Your open tasks:*"]
        for i, t in enumerate(tasks, 1):
            due = f" · due {t['due_date']}" if t.get("due_date") else ""
            ref = (t.get("task_ref") or "").lstrip("#") or str(t.get("id") or "")[:8]
            lines.append(f"{i}. [{t.get('status')}] {t.get('title')}{due}\n   Reply: done #{ref}")
        lines.append(
            "\nCommands: *accept*, *start*, *done*, *unable* "
            "(add #id if you have several)."
        )
        notification_service.send_whatsapp_text(phone_digits, "\n".join(lines))
        return True

    q = _extract_task_query(text_body)
    # Resolve target for photo-proof gate before mutating
    preview = find_tasks(
        ctx,
        mine_only=True,
        status="OPEN",
        q=q,
        task_id=q if re.fullmatch(r"[0-9a-fA-F-]{8,}", q or "") else "",
        limit=5,
    )
    task_row = None
    if preview.success:
        rows = (preview.data or {}).get("tasks") or []
        if len(rows) == 1:
            task_row = rows[0]
        elif q and rows:
            task_row = rows[0]

    if intent == "COMPLETED" and task_row:
        # Load require_photo_proof from DB
        try:
            from dashboard.models import Task

            task_obj = Task.objects.filter(id=task_row["id"], restaurant=ctx.restaurant).first()
        except Exception:
            task_obj = None
        if (
            task_obj
            and getattr(task_obj, "require_photo_proof", False)
            and not getattr(task_obj, "proof_media_url", None)
        ):
            if session is not None:
                try:
                    session.context["awaiting_dashboard_task_proof_id"] = str(task_obj.id)
                    session.context["awaiting_dashboard_task_proof_complete"] = True
                    session.save(update_fields=["context"])
                except Exception:
                    logger.exception(
                        "dashboard_task_whatsapp: failed to arm proof session task=%s",
                        task_obj.id,
                    )
            notification_service.send_whatsapp_text(
                phone_digits,
                f"Before I mark *{task_obj.title}* done, please send a photo as proof "
                "(you can add a short caption with it).",
            )
            return True

    result = apply_task_status(
        user=user,
        channel="whatsapp",
        status=intent,
        task_id=str(task_row["id"]) if task_row else "",
        q=q if not task_row else "",
        restaurant=ctx.restaurant,
        assignee_scope=True,
        notify_managers=True,
        message_id=f"whatsapp:task:{intent}:{q or (task_row or {}).get('id', '')}",
    )
    if result.needs_clarification:
        cands = (result.data or {}).get("candidates") or []
        if cands:
            lines = [result.message_for_user or "Which task?"]
            for c in cands[:5]:
                if isinstance(c, dict):
                    lines.append(f"- {c.get('task_ref')} {c.get('title')} ({c.get('status')})")
            notification_service.send_whatsapp_text(phone_digits, "\n".join(lines))
        else:
            notification_service.send_whatsapp_text(
                phone_digits,
                result.message_for_user or "Which task should I update?",
            )
        return True

    if not result.success:
        notification_service.send_whatsapp_text(
            phone_digits,
            result.message_for_user
            or "I couldn't update that task. Reply *tasks* to list yours.",
        )
        return True

    task = (result.data or {}).get("task") or {}
    label = intent.replace("_", " ").title()
    notification_service.send_whatsapp_text(
        phone_digits,
        f"Updated *{task.get('title') or 'task'}* → {label}.",
    )
    return True


# Back-compat for views that still import this name
def _notify_managers_completed(task, acting_user) -> None:
    from miya.services.ops.tasks import _notify_managers_task_outcome

    _notify_managers_task_outcome(task, acting_user, kind="COMPLETED")
