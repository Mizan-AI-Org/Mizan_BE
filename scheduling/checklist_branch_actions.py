"""
Runtime evaluation of Processes & Tasks Yes/No condition flow.

When staff answers No (or Yes) with ``type: alert`` ("Flag for manager, then continue"),
create URGENT dashboard tasks for configured assignees (or all managers) and notify
them via in-app + WhatsApp (Miya message channel).
"""
from __future__ import annotations

import logging
import re
from typing import Any

from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

_MANAGER_ROLES = (
    "MANAGER",
    "ADMIN",
    "OWNER",
    "SUPER_ADMIN",
    "RESTAURANT_OWNER",
    "GENERAL_MANAGER",
)


def resolve_branch_action(shift_task, answer: str) -> dict[str, Any] | None:
    """
    Return the branch action dict for yes|no, or None if no branch applies.
    """
    answer = (answer or "").strip().lower()
    if answer not in ("yes", "no"):
        return None

    cfg = getattr(shift_task, "branch_config", None) or {}
    if isinstance(cfg, dict):
        branches = cfg.get("branches") or {}
        if isinstance(branches, dict):
            action = branches.get(answer)
            if isinstance(action, dict) and action.get("type"):
                return action

    # Fallback for shifts instantiated before branch_config existed
    try:
        shift = shift_task.shift
        title = (shift_task.title or "").strip().lower()
        templates = shift.task_templates.all() if hasattr(shift, "task_templates") else []
        for tmpl in templates:
            for item in getattr(tmpl, "tasks", None) or []:
                if not isinstance(item, dict):
                    continue
                item_title = str(item.get("title") or item.get("name") or "").strip().lower()
                if item_title and item_title == title:
                    br = (item.get("branches") or {}).get(answer)
                    if isinstance(br, dict) and br.get("type"):
                        return br
    except Exception:
        logger.exception("resolve_branch_action template fallback failed task=%s", getattr(shift_task, "id", None))

    return None


def _manager_qs(restaurant):
    from accounts.models import CustomUser

    return CustomUser.objects.filter(
        restaurant=restaurant,
        is_active=True,
    ).filter(
        Q(role__in=_MANAGER_ROLES)
        | Q(role__icontains="MANAGER")
        | Q(role__icontains="OWNER")
        | Q(role__icontains="ADMIN")
    )


def _resolve_assignees(restaurant, assignee_ids: list[str] | None):
    """Resolve assignee user ids; empty list → all managers/admins."""
    from accounts.models import CustomUser

    ids = [str(x).strip() for x in (assignee_ids or []) if str(x).strip()]
    if not ids:
        return list(_manager_qs(restaurant).distinct()[:25])

    users = list(
        CustomUser.objects.filter(restaurant=restaurant, is_active=True, id__in=ids)
    )
    # Staff API sometimes returns nested user ids inconsistently — also try
    # matching when the stored id is not a restaurant user yet.
    if len(users) < len(ids):
        found = {str(u.id) for u in users}
        missing = [i for i in ids if i not in found]
        if missing:
            extra = CustomUser.objects.filter(is_active=True, id__in=missing).filter(
                Q(restaurant=restaurant) | Q(restaurant__isnull=True)
            )
            for u in extra:
                if str(u.id) not in found:
                    users.append(u)
                    found.add(str(u.id))
    return users


def _is_manager_or_admin(user) -> bool:
    role = str(getattr(user, "role", "") or "").upper()
    if not role:
        return False
    if role in _MANAGER_ROLES:
        return True
    return "MANAGER" in role or "ADMIN" in role or "OWNER" in role


def execute_alert_branch(
    *,
    shift_task,
    staff_user,
    answer: str,
    action: dict[str, Any],
) -> dict[str, Any]:
    """
    Create URGENT dashboard.Task rows + notify assignees (WhatsApp + in-app).

    Returns a summary dict for agent replies / logging.
    """
    from dashboard.models import Task
    from notifications.services import notification_service

    if not isinstance(action, dict) or str(action.get("type") or "").lower() != "alert":
        return {"executed": False, "reason": "not_alert"}

    shift = shift_task.shift
    restaurant = getattr(getattr(shift, "schedule", None), "restaurant", None) or getattr(
        staff_user, "restaurant", None
    )
    if restaurant is None:
        try:
            restaurant = shift.schedule.restaurant
        except Exception:
            return {"executed": False, "reason": "no_restaurant"}

    note = str(action.get("message") or "Needs attention").strip() or "Needs attention"
    staff_name = (
        f"{(getattr(staff_user, 'first_name', None) or '').strip()} "
        f"{(getattr(staff_user, 'last_name', None) or '').strip()}"
    ).strip() or (getattr(staff_user, "email", None) or "Staff")

    title = f"Checklist flag: {shift_task.title}"[:255]
    description = (
        f"{staff_name} answered *{answer.upper()}* on checklist task "
        f"\"{shift_task.title}\".\n"
        f"Status: {note}\n"
        f"Shift: {getattr(shift, 'shift_date', '') or ''}"
    ).strip()

    marker = f"shift_task:{shift_task.id}:{answer}"
    assignees = _resolve_assignees(restaurant, action.get("assignees"))
    if not assignees:
        return {"executed": False, "reason": "no_assignees", "notified": []}

    created_ids: list[str] = []
    notified: list[dict[str, Any]] = []

    for assignee in assignees:
        # Idempotent: don't spam if the same No is reprocessed
        existing = Task.objects.filter(
            restaurant=restaurant,
            assigned_to=assignee,
            status__in=("PENDING", "IN_PROGRESS"),
            ai_summary__contains=marker,
        ).first()
        if existing:
            created_ids.append(str(existing.id))
            continue

        task = Task.objects.create(
            restaurant=restaurant,
            assigned_to=assignee,
            title=title,
            description=description,
            priority="URGENT",
            status="PENDING",
            due_date=timezone.now().date(),
            source="SYSTEM",
            source_label="Checklist condition",
            ai_summary=f"{note} · {marker}",
            category="OPERATIONS",
            follow_up_enabled=True,
        )
        created_ids.append(str(task.id))

        # In-app / push
        try:
            notification_service.send_custom_notification(
                recipient=assignee,
                message=f"Urgent: {title} — {note}",
                title="Checklist needs attention",
                notification_type="TASK_ASSIGNED",
                channels=["app", "push"],
                sender=staff_user,
            )
        except Exception:
            logger.exception("checklist alert: in-app notify failed assignee=%s", assignee.pk)

        wa_sent = False
        phone = re.sub(r"\D", "", str(getattr(assignee, "phone", "") or ""))
        if phone:
            body = (
                f"🚨 *Urgent checklist flag*\n\n"
                f"*{shift_task.title}*\n"
                f"{staff_name} answered *{answer.upper()}*.\n"
                f"Note: {note}\n\n"
                f"I've assigned this to you as an *URGENT* task"
                + (
                    " — check your Urgent widget on the dashboard."
                    if _is_manager_or_admin(assignee)
                    else "."
                )
            )
            try:
                result = notification_service.send_whatsapp_text(phone, body)
                wa_sent = result[0] if isinstance(result, tuple) else bool(result)
                if wa_sent:
                    task.whatsapp_notified_at = timezone.now()
                    task.save(update_fields=["whatsapp_notified_at"])
            except Exception:
                logger.exception("checklist alert: WhatsApp failed assignee=%s", assignee.pk)

        notified.append(
            {
                "user_id": str(assignee.id),
                "name": f"{(assignee.first_name or '').strip()} {(assignee.last_name or '').strip()}".strip(),
                "role": getattr(assignee, "role", ""),
                "is_manager": _is_manager_or_admin(assignee),
                "whatsapp_sent": wa_sent,
                "task_id": str(task.id),
            }
        )

    return {
        "executed": True,
        "type": "alert",
        "message": note,
        "task_ids": created_ids,
        "notified": notified,
        "continue_checklist": True,
    }


def apply_checklist_branch(
    *,
    shift_task,
    staff_user,
    answer: str,
) -> dict[str, Any]:
    """
    Resolve + execute branch for a checklist answer.

    Returns:
      { action, result, flow: "next"|"end"|"goto", goto_task_id? }
    """
    action = resolve_branch_action(shift_task, answer)
    if not action:
        return {"action": None, "result": None, "flow": "next"}

    action_type = str(action.get("type") or "next").lower()
    if action_type == "alert":
        result = execute_alert_branch(
            shift_task=shift_task,
            staff_user=staff_user,
            answer=answer,
            action=action,
        )
        return {"action": action, "result": result, "flow": "next"}

    if action_type == "end":
        return {"action": action, "result": {"executed": True, "type": "end"}, "flow": "end"}

    if action_type == "goto":
        target = str(action.get("task_id") or "").strip()
        return {
            "action": action,
            "result": {"executed": True, "type": "goto", "task_id": target},
            "flow": "goto",
            "goto_task_id": target,
        }

    return {"action": action, "result": None, "flow": "next"}
