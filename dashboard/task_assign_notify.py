"""
Shared task assignment notifications (in-app + WhatsApp).

Used by Miya agent create/reassign, dashboard UI reassign, and automations.
"""
from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from django.utils import timezone

if TYPE_CHECKING:
    from accounts.models import CustomUser
    from dashboard.models import Task

logger = logging.getLogger(__name__)


def _format_due(due_date) -> str:
    if not due_date:
        return "no due date"
    return due_date.strftime("%Y-%m-%d")


def build_task_whatsapp_body(
    task: "Task",
    *,
    sender_name: str,
    assignee_first_name: str = "",
    informed: bool = False,
    override: str | None = None,
) -> str:
    if override and str(override).strip():
        return str(override).strip()

    hello = f"Hi {assignee_first_name}," if assignee_first_name else "Hi,"
    pretty_priority = {
        "URGENT": "URGENT priority",
        "HIGH": "high priority",
        "MEDIUM": "medium priority",
        "LOW": "low priority",
    }.get(task.priority or "MEDIUM", "")

    if informed:
        primary = getattr(task, "assigned_to", None)
        primary_name = ""
        if primary:
            primary_name = (
                f"{primary.first_name or ''} {primary.last_name or ''}".strip()
                or (primary.email or "")
            )
        lines = [
            hello,
            "",
            f"ℹ️ FYI — new task in your category from {sender_name}:",
            f"*{task.title}*",
        ]
        if task.description:
            lines.append(str(task.description))
        if primary_name:
            lines.append(f"\n*Assigned to:* {primary_name}")
        lines.extend(["", "Open your dashboard to review."])
        return "\n".join(lines)

    lines = [
        hello,
        "",
        f"New task from {sender_name}: *{task.title}*",
    ]
    if task.description:
        lines.append(str(task.description))
    meta_bits = []
    if pretty_priority:
        meta_bits.append(pretty_priority)
    meta_bits.append(f"due {_format_due(task.due_date)}")
    lines.append("")
    lines.append(f"({'; '.join(meta_bits)})")
    lines.append("")
    lines.append("Reply *accept*, *start*, *done*, or *unable* (add #id if you have several).")
    return "\n".join(lines)


def notify_task_assignment(
    task: "Task",
    *,
    assignee: "CustomUser",
    sender: "CustomUser | None" = None,
    sender_display: str = "",
    informed_owners: list["CustomUser"] | None = None,
    notify_whatsapp: bool = True,
    whatsapp_override: str | None = None,
    is_reassignment: bool = False,
) -> dict[str, Any]:
    """
    Notify primary assignee and optional informed category owners.
    Returns a summary dict with whatsapp / in_app results.
    """
    from notifications.services import notification_service

    informed_owners = informed_owners or []
    sender_name = (sender_display or "").strip()
    if not sender_name and sender:
        sender_name = (
            f"{(sender.first_name or '').strip()} {(sender.last_name or '').strip()}".strip()
            or (sender.email or "")
        )
    if not sender_name:
        sender_name = "Miya"

    result: dict[str, Any] = {
        "primary_in_app": False,
        "primary_whatsapp": {"sent": False, "skipped_reason": None, "error": None},
        "informed_notified": [],
    }

    title = "Task reassigned to you" if is_reassignment else "New task assigned"
    try:
        notification_service.send_custom_notification(
            recipient=assignee,
            message=(
                f"{'Reassigned' if is_reassignment else 'New'} task: {task.title}"
                + (f" (due {_format_due(task.due_date)})" if task.due_date else "")
            ),
            title=title,
            notification_type="TASK_ASSIGNED",
            channels=["app", "push"],
            sender=sender,
        )
        result["primary_in_app"] = True
    except Exception:
        logger.exception("task_assign_notify: primary in-app failed task=%s", task.id)

    wa = result["primary_whatsapp"]
    if not notify_whatsapp:
        wa["skipped_reason"] = "disabled"
    elif not (assignee.phone or "").strip():
        wa["skipped_reason"] = "no_phone"
        wa["error"] = f"{assignee.first_name or 'Staff member'} has no phone number on file."
    else:
        body = build_task_whatsapp_body(
            task,
            sender_name=sender_name,
            assignee_first_name=(assignee.first_name or "").strip(),
            informed=False,
            override=whatsapp_override,
        )
        try:
            ok, resp = notification_service.send_whatsapp_text(assignee.phone, body)
            wa["sent"] = bool(ok)
            if not ok and isinstance(resp, dict):
                wa["error"] = resp.get("error") or "WhatsApp send failed"
            if ok:
                task.whatsapp_notified_at = timezone.now()
                task.save(update_fields=["whatsapp_notified_at"])
        except Exception as exc:
            logger.exception("task_assign_notify: primary WA failed task=%s", task.id)
            wa["error"] = str(exc)[:200]

    seen = {str(assignee.id)}
    for owner in informed_owners:
        oid = str(owner.id)
        if oid in seen:
            continue
        seen.add(oid)
        entry = {"user_id": oid, "in_app": False, "whatsapp": False}
        try:
            notification_service.send_custom_notification(
                recipient=owner,
                message=f"New task in your category: {task.title}",
                title="Category task FYI",
                notification_type="TASK_ASSIGNED",
                channels=["app", "push"],
                sender=sender,
            )
            entry["in_app"] = True
        except Exception:
            logger.exception("task_assign_notify: informed in-app failed user=%s", oid)

        if notify_whatsapp and (owner.phone or "").strip():
            try:
                fyi_body = build_task_whatsapp_body(
                    task,
                    sender_name=sender_name,
                    assignee_first_name=(owner.first_name or "").strip(),
                    informed=True,
                )
                ok, _ = notification_service.send_whatsapp_text(owner.phone, fyi_body)
                entry["whatsapp"] = bool(ok)
            except Exception:
                logger.exception("task_assign_notify: informed WA failed user=%s", oid)
        result["informed_notified"].append(entry)

    return result


def notify_new_task_assignees(
    task: "Task",
    assignee_users: list["CustomUser"],
    *,
    sender: "CustomUser | None" = None,
    sender_display: str = "",
    previous_ids: set[str] | None = None,
    notify_whatsapp: bool = True,
) -> dict[str, Any]:
    """WhatsApp + in-app for each newly added assignee (skips unchanged)."""
    prev = previous_ids or set()
    summary: dict[str, Any] = {"notified": [], "skipped": []}
    for user in assignee_users:
        uid = str(user.id)
        if uid in prev:
            summary["skipped"].append(uid)
            continue
        result = notify_task_assignment(
            task,
            assignee=user,
            sender=sender,
            sender_display=sender_display,
            informed_owners=[],
            notify_whatsapp=notify_whatsapp,
        )
        summary["notified"].append({"user_id": uid, "whatsapp": result.get("primary_whatsapp")})
    return summary


def notify_task_reassignment(
    task: "Task",
    new_assignee: "CustomUser",
    *,
    sender: "CustomUser | None" = None,
    old_assignee: "CustomUser | None" = None,
    note: str = "",
) -> dict[str, Any]:
    sender_display = ""
    if sender:
        sender_display = (
            f"{(sender.first_name or '').strip()} {(sender.last_name or '').strip()}".strip()
            or (sender.email or "")
        )
    override = None
    if note:
        override = (
            f"Hi {(new_assignee.first_name or '').strip() or 'there'},\n\n"
            f"You've been assigned: *{task.title}*"
            + (f"\n{task.description}" if task.description else "")
            + f"\n\nNote from manager: {note}"
            + "\n\nReply *accept*, *start*, *done*, or *unable*."
        )
    return notify_task_assignment(
        task,
        assignee=new_assignee,
        sender=sender,
        sender_display=sender_display or "Dashboard",
        informed_owners=[],
        notify_whatsapp=True,
        whatsapp_override=override,
        is_reassignment=True,
    )
