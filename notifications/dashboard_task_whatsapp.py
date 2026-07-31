"""
WhatsApp lifecycle for ``dashboard.Task`` (Miya-assigned ops tasks).

Staff can reply with accept / start / done / unable so status syncs to the
dashboard without waiting for Mastra. Managers still use Miya for create/reassign.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from django.utils import timezone

logger = logging.getLogger(__name__)

_OPEN = ("PENDING", "ACCEPTED", "IN_PROGRESS")


def looks_like_dashboard_task_status_reply(text: str | None) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    if re.match(
        r"^(done|complete|completed|finish|finished|accept|accepted|"
        r"start|started|progress|unable|can'?t|cannot|reject|rejected|"
        r"cancel|cancelled|my tasks|tasks|list tasks|"
        r"terminer|accepté|commencer|incapable|"
        r"إتمام|قبول|بدء|تعذر)\b",
        t,
    ):
        return True
    if re.match(r"^(done|complete|accept|start|unable)\s+#?\d", t):
        return True
    return False


def _normalize_status_intent(text: str) -> str | None:
    t = (text or "").strip().lower()
    if re.search(r"\b(done|complete|completed|finish|finished|terminer|إتمام)\b", t):
        return "COMPLETED"
    if re.search(r"\b(unable|can'?t complete|cannot complete|incapable|تعذر)\b", t):
        return "UNABLE_TO_COMPLETE"
    if re.search(r"\b(reject|rejected|cancel|cancelled)\b", t):
        return "CANCELLED"
    if re.search(r"\b(accept|accepted|قبول|accepté)\b", t):
        return "ACCEPTED"
    if re.search(r"\b(start|started|progress|in progress|commencer|بدء)\b", t):
        return "IN_PROGRESS"
    if re.search(r"\b(my tasks|tasks|list tasks)\b", t):
        return "LIST"
    return None


def _pick_task(user, text: str):
    from dashboard.models import Task

    qs = (
        Task.objects.filter(assigned_to=user, status__in=_OPEN)
        .order_by("-updated_at")
    )
    m = re.search(r"#?([0-9a-f]{8})", text.lower())
    if m:
        ref = m.group(1)
        for task in qs[:40]:
            if str(task.id).replace("-", "").startswith(ref):
                return task
    return qs.first()


def _notify_managers_completed(task, acting_user) -> None:
    try:
        from notifications.services import notification_service
        from accounts.models import CustomUser

        restaurant = task.restaurant
        managers = CustomUser.objects.filter(
            restaurant=restaurant,
            is_active=True,
            role__in=("SUPER_ADMIN", "OWNER", "ADMIN", "MANAGER"),
        ).exclude(pk=getattr(acting_user, "id", None))[:8]
        actor = ""
        if acting_user:
            actor = (
                f"{(acting_user.first_name or '').strip()} "
                f"{(acting_user.last_name or '').strip()}"
            ).strip() or acting_user.email
        for mgr in managers:
            notification_service.send_custom_notification(
                recipient=mgr,
                message=f"{actor or 'Staff'} completed: {task.title}",
                title="Task completed",
                notification_type="TASK_COMPLETED",
                channels=["app", "push"],
                sender=acting_user,
            )
            if (mgr.phone or "").strip():
                try:
                    notification_service.send_whatsapp_text(
                        mgr.phone,
                        f"✅ Task completed by {actor or 'staff'}: *{task.title}*",
                    )
                except Exception:
                    pass
    except Exception:
        logger.exception("dashboard_task_whatsapp: manager notify failed task=%s", task.id)


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
    """
    if not user or not text_body:
        return False
    intent = _normalize_status_intent(text_body)
    if not intent:
        return False

    from dashboard.models import Task

    if intent == "LIST":
        tasks = list(
            Task.objects.filter(assigned_to=user, status__in=_OPEN).order_by("due_date", "-created_at")[:15]
        )
        if not tasks:
            notification_service.send_whatsapp_text(
                phone_digits,
                "No open tasks assigned to you right now.",
            )
            return True
        lines = ["*Your open tasks:*"]
        for i, t in enumerate(tasks, 1):
            due = f" · due {t.due_date}" if t.due_date else ""
            ref = str(t.id).replace("-", "")[:8]
            lines.append(f"{i}. [{t.status}] {t.title}{due}\n   Reply: done #{ref}")
        lines.append(
            "\nCommands: *accept*, *start*, *done*, *unable* "
            "(add #id if you have several)."
        )
        notification_service.send_whatsapp_text(phone_digits, "\n".join(lines))
        return True

    task = _pick_task(user, text_body)
    if not task:
        notification_service.send_whatsapp_text(
            phone_digits,
            "I couldn't find an open task for you. Reply *tasks* to list them.",
        )
        return True

    if (
        intent == "COMPLETED"
        and getattr(task, "require_photo_proof", False)
        and not getattr(task, "proof_media_url", None)
    ):
        if session is not None:
            try:
                session.context["awaiting_dashboard_task_proof_id"] = str(task.id)
                session.context["awaiting_dashboard_task_proof_complete"] = True
                session.save(update_fields=["context"])
            except Exception:
                logger.exception(
                    "dashboard_task_whatsapp: failed to arm proof session task=%s", task.id
                )
        notification_service.send_whatsapp_text(
            phone_digits,
            f"Before I mark *{task.title}* done, please send a photo as proof "
            "(you can add a short caption with it).",
        )
        return True

    old = task.status
    task.status = intent
    update_fields = ["status", "updated_at"]
    if intent == "COMPLETED":
        if hasattr(task, "completed_at"):
            task.completed_at = timezone.now()
            update_fields.append("completed_at")
        if hasattr(task, "completed_by_id"):
            task.completed_by = user
            update_fields.append("completed_by")
    task.save(update_fields=update_fields)

    try:
        from dashboard.task_sync import broadcast_tasks_invalidate

        broadcast_tasks_invalidate(task.restaurant, reason="whatsapp_task_status", task_id=str(task.id))
    except Exception:
        pass

    label = intent.replace("_", " ").title()
    notification_service.send_whatsapp_text(
        phone_digits,
        f"Updated *{task.title}* → {label}.",
    )
    if intent == "COMPLETED" and old != "COMPLETED":
        _notify_managers_completed(task, user)
    elif intent == "UNABLE_TO_COMPLETE":
        try:
            from accounts.models import CustomUser

            actor = (
                f"{(user.first_name or '').strip()} {(user.last_name or '').strip()}".strip()
                or user.email
            )
            for mgr in CustomUser.objects.filter(
                restaurant=task.restaurant,
                is_active=True,
                role__in=("SUPER_ADMIN", "OWNER", "ADMIN", "MANAGER"),
            )[:6]:
                notification_service.send_custom_notification(
                    recipient=mgr,
                    message=f"{actor} cannot complete: {task.title}",
                    title="Task unable to complete",
                    notification_type="TASK_ASSIGNED",
                    channels=["app", "push"],
                    sender=user,
                )
                if (mgr.phone or "").strip():
                    try:
                        notification_service.send_whatsapp_text(
                            mgr.phone,
                            f"⚠️ {actor} marked unable to complete: *{task.title}*",
                        )
                    except Exception:
                        pass
        except Exception:
            logger.exception("unable notify failed")
    return True
