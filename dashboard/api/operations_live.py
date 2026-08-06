"""
Operations Live - unified daily operations feed for the manager dashboard.

Powers the full-page "Operations Live" view between Locations Overview and
Processes & Tasks. Merges every operational record Miya can create or
update into three lanes (new demands / in progress / completed) with the
rich row shape the UI needs (from, to, category, escalated_to, attachment).

Sources:
- ``dashboard.Task`` - Miya / WhatsApp / manual tasks
- ``staff.StaffRequest`` - WhatsApp / voice staff inbox
- ``scheduling.Task`` - shift checklist tasks
- ``finance.Invoice`` - bills logged by Miya

Status updates reuse the existing ``/api/dashboard/tasks-demands/<id>/status/``
and assignee endpoints - the frontend passes the row ``kind`` only for UI.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.db.models import Q
from django.utils import timezone
from rest_framework import permissions, status as http_status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView

from core.http_caching import json_response_with_cache

from ..models import Task
from .category_tasks import (
    _PRIORITY_RANK,
    _PRIORITY_RANK_MAP,
    _age_label,
    _serialize_dashboard_task,
    _serialize_invoice,
    _serialize_staff_request,
)
from .tasks_demands import _serialize_scheduling_task

DEFAULT_LIMIT = 50
MAX_LIMIT = 100

_SCHED_STATUS_TO_WIDGET = {
    "TODO": "PENDING",
    "IN_PROGRESS": "IN_PROGRESS",
    "COMPLETED": "COMPLETED",
    "CANCELLED": "CANCELLED",
}

_STAFF_PENDING = ("PENDING", "ESCALATED")
_STAFF_IN_PROGRESS = ("APPROVED", "WAITING_ON")


_ROLE_LABELS = {
    "SUPER_ADMIN": "super admin",
    "ADMIN": "admin",
    "OWNER": "owner",
    "MANAGER": "manager",
    "SUPERVISOR": "supervisor",
    "CHEF": "chef",
    "WAITER": "waiter",
    "CASHIER": "cashier",
    "KITCHEN_STAFF": "kitchen",
    "CLEANER": "cleaner",
    "DELIVERY": "delivery",
    "CUSTOM": "staff",
}


def _humanize_role(role: str | None) -> str | None:
    if not role:
        return None
    key = str(role).strip().upper()
    if key in _ROLE_LABELS:
        return _ROLE_LABELS[key]
    return key.replace("_", " ").lower()


def _user_display(user) -> tuple[str, str | None]:
    if not user:
        return "", None
    first = (getattr(user, "first_name", None) or "").strip()
    last = (getattr(user, "last_name", None) or "").strip()
    name = f"{first} {last}".strip() or (getattr(user, "email", None) or "")
    role = _humanize_role(getattr(user, "role", None))
    return name, role


def _to_payload(user, current_user) -> dict[str, Any]:
    name, role = _user_display(user)
    if not name:
        return {"name": "-", "is_me": False, "role": None}
    uid = str(getattr(user, "pk", "") or "")
    is_me = bool(current_user and uid and str(current_user.pk) == uid)
    return {
        "id": uid or None,
        "name": "Me" if is_me else name,
        "is_me": is_me,
        "role": role,
    }


def _attachment_for_dashboard_task(task) -> tuple[str | None, str | None]:
    url = (getattr(task, "attachment_url", None) or "").strip()
    if not url and getattr(task, "attachment", None):
        try:
            url = task.attachment.url
        except Exception:
            url = ""
    if url:
        label = "document"
        lower = url.lower()
        if any(ext in lower for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
            label = "picture"
        elif "invoice" in lower:
            label = "invoice"
        elif "contract" in lower:
            label = "contract"
        return label, url
    proof = (getattr(task, "proof_media_url", None) or "").strip()
    if proof:
        return "picture", proof
    return None, None


def _attachment_for_staff_request(req) -> tuple[str | None, str | None]:
    voice = (getattr(req, "voice_audio_url", None) or "").strip()
    if voice:
        return "voice", voice
    md = getattr(req, "metadata", None) or {}
    for key in ("attachment_url", "document_url", "photo_url"):
        url = (md.get(key) or "").strip()
        if url:
            return "document", url
    return None, None


def _attachment_for_invoice(inv) -> tuple[str | None, str | None]:
    for field in ("attachment", "photo"):
        f = getattr(inv, field, None)
        if f:
            try:
                return "invoice", f.url
            except Exception:
                pass
    photo_url = (getattr(inv, "photo_url", None) or "").strip()
    if photo_url:
        return "invoice", photo_url
    return None, None


def _display_status(item: dict[str, Any], lane: str) -> str:
    if lane == "completed":
        return "completed"
    if lane == "in_progress":
        return "in_progress"
    priority = (item.get("priority") or "").upper()
    pill = (item.get("pill_status") or "").upper()
    if priority == "URGENT" or pill == "ESCALATED" or pill == "OVERDUE":
        return "critical"
    return "pending"


def _escalated_to(
    item: dict[str, Any],
    *,
    obj=None,
    current_user=None,
    to_payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    assignee = item.get("assignee") or {}
    pill = (item.get("pill_status") or "").upper()
    raw = (item.get("raw_status") or "").upper()
    status = (item.get("status") or "").upper()
    priority = (item.get("priority") or "").upper()

    # Only surface Escalated To when the item was actually escalated /
    # overdue / blocked - not merely because priority is URGENT (that made
    # every critical row look "escalated to Me" when the assignee is self).
    show = (
        pill in {"ESCALATED", "OVERDUE"}
        or raw == "ESCALATED"
        or status == "UNABLE_TO_COMPLETE"
    )
    if not show:
        return None

    if obj is not None and hasattr(obj, "metadata"):
        md = getattr(obj, "metadata", None) or {}
        name = (md.get("escalated_assignee_name") or "").strip()
        if name:
            role = None
            if getattr(obj, "assignee", None):
                _, role = _user_display(obj.assignee)
            return {"name": name, "role": role}

    # Prefer the same "Me" / role formatting as the TO column.
    if to_payload and to_payload.get("name") and to_payload.get("name") != "-":
        return {
            "name": to_payload["name"],
            "role": None if to_payload.get("is_me") else to_payload.get("role"),
        }

    if assignee.get("name"):
        uid = str(assignee.get("id") or "")
        is_me = bool(current_user and uid and str(current_user.pk) == uid)
        return {
            "name": "Me" if is_me else assignee["name"],
            "role": None if is_me else _humanize_role(assignee.get("role")),
        }
    return None


_CHANNEL_FROM_LABELS = frozenset(
    {
        "",
        "miya",
        "miya ai",
        "manual",
        "system",
        "whatsapp",
        "email",
        "inbox",
        "invoice",
        "scheduling",
        "finance · invoice assign",
    }
)


def _human_from_source_label(label: str | None) -> str | None:
    """Extract the person from labels like ``Miya AI · Hamza Hadni``."""
    text = (label or "").strip()
    if not text:
        return None
    if "·" in text:
        person = text.split("·", 1)[1].strip()
        if person and person.lower() not in _CHANNEL_FROM_LABELS:
            return person
    # Spaced hyphen only (legacy "Miya AI - Name"); never split on hyphens in names.
    if " - " in text:
        person = text.split(" - ", 1)[1].strip()
        if person and person.lower() not in _CHANNEL_FROM_LABELS:
            return person
    if text.lower() in _CHANNEL_FROM_LABELS:
        return None
    # Bare human-looking label (not a channel name).
    return text


def _enrich_row(
    item: dict[str, Any],
    *,
    lane: str,
    current_user,
    obj=None,
) -> dict[str, Any]:
    kind = item.get("kind") or "dashboard"
    from_name = ""
    from_role = None

    if kind == "staff_request" and obj is not None:
        # Staff inbox: requester is always the staff member, never Miya.
        if getattr(obj, "staff", None):
            from_name, from_role = _user_display(obj.staff)
        elif (getattr(obj, "staff_name", None) or "").strip():
            from_name = obj.staff_name.strip()
        else:
            from_name = "Staff"
    elif kind == "invoice":
        # From = who logged / submitted the bill; To = payment owner.
        creator = getattr(obj, "created_by", None) if obj else None
        from_name, from_role = _user_display(creator)
        if not from_name:
            from_name = _human_from_source_label(item.get("source_label")) or "-"
    elif kind == "scheduling":
        from_name = "Scheduling"
    else:
        # Dashboard tasks: From = who asked (created_by / embedded label).
        # Never treat Miya as the sender - she is the channel.
        creator = getattr(obj, "created_by", None) if obj else None
        if creator is not None:
            from_name, from_role = _user_display(creator)
        if not from_name:
            from_name = _human_from_source_label(item.get("source_label")) or ""
        # Legacy rows: fall back to the custom-widget owner (manager who
        # asked Miya to build the Wedding/process lane) before bare "-".
        if not from_name and obj is not None:
            cw = getattr(obj, "custom_widget", None)
            cw_owner = getattr(cw, "created_by", None) or getattr(cw, "user", None)
            if cw_owner is not None:
                from_name, from_role = _user_display(cw_owner)
        if not from_name:
            source = (item.get("source") or "").upper()
            if source == "WHATSAPP":
                from_name = "Staff"
            elif source == "EMAIL":
                from_name = "Email"
            elif source == "MANUAL":
                from_name = "Manager"
            else:
                from_name = "-"

    item["from"] = {"name": from_name or "-", "role": from_role}

    # To = assignee / payment owner only. Do not fall back to the requester
    # (that conflates From and To when a manager assigns to themselves).
    assignee_user = None
    if obj is not None:
        if kind == "staff_request":
            assignee_user = getattr(obj, "assignee", None)
        elif kind == "dashboard":
            try:
                assignees = list(obj.assignees.all())
            except Exception:
                assignees = []
            if not assignees and getattr(obj, "assigned_to", None):
                assignees = [obj.assigned_to]
            if len(assignees) > 1:
                first = assignees[0]
                first_name = (
                    f"{(first.first_name or '').strip()} {(first.last_name or '').strip()}".strip()
                    or (first.email or "")
                )
                assignee_user = first
                item["_assignee_count"] = len(assignees)
            else:
                assignee_user = assignees[0] if assignees else getattr(obj, "assigned_to", None)
        elif kind == "scheduling" and getattr(obj, "pk", None):
            assignee_user = obj.assigned_to.all().first()
        elif kind == "invoice":
            assignee_user = getattr(obj, "assigned_to", None)

    to_payload = _to_payload(assignee_user, current_user)
    if not assignee_user:
        to_payload = {"name": "-", "is_me": False, "role": None, "id": None}
    elif item.get("_assignee_count", 0) > 1:
        to_payload = {
            **to_payload,
            "name": f"{to_payload.get('name', '-')} +{item['_assignee_count'] - 1}",
        }
    item["to"] = to_payload

    attach_label, attach_url = None, None
    if kind == "dashboard" and obj is not None:
        attach_label, attach_url = _attachment_for_dashboard_task(obj)
    elif kind == "staff_request" and obj is not None:
        attach_label, attach_url = _attachment_for_staff_request(obj)
    elif kind == "invoice" and obj is not None:
        attach_label, attach_url = _attachment_for_invoice(obj)

    item["attachment_label"] = attach_label
    item["attachment_url"] = attach_url
    item["display_status"] = _display_status(item, lane)
    item["escalated_to"] = _escalated_to(
        item, obj=obj, current_user=current_user, to_payload=to_payload
    )
    item["operation"] = item.get("title") or item.get("description") or ""
    # Soft-delete = CANCELLED. Surface so Miya / UI can remove from live lanes.
    item["can_cancel"] = (item.get("status") or "").upper() not in {
        "COMPLETED",
        "CANCELLED",
    }

    # Process / custom dashboard widget name (Wedding, etc.) so the UI can
    # show the same lane label managers see on the main dashboard.
    process_label = None
    if kind == "dashboard" and obj is not None:
        cw = getattr(obj, "custom_widget", None)
        if cw is not None:
            process_label = (getattr(cw, "title", None) or "").strip() or None
            if process_label and not (item.get("category") or "").strip():
                item["category"] = "PROCESS"
    item["process_label"] = process_label

    return item


def _matches_search(
    item: dict[str, Any],
    query: str,
    search_by: str,
) -> bool:
    q = query.lower().strip()
    if not q:
        return True

    if search_by == "staff":
        hay = " ".join(
            filter(
                None,
                [
                    (item.get("from") or {}).get("name"),
                    (item.get("from") or {}).get("role"),
                    (item.get("to") or {}).get("name"),
                    (item.get("escalated_to") or {}).get("name"),
                    (item.get("assignee") or {}).get("name"),
                ],
            )
        ).lower()
        return q in hay

    if search_by == "category":
        cat = (item.get("category") or "").lower()
        process = (item.get("process_label") or "").lower()
        return (
            q in cat
            or q in process
            or q in (item.get("source_label") or "").lower()
        )

    # task (default)
    hay = " ".join(
        filter(
            None,
            [
                item.get("title"),
                item.get("description"),
                item.get("ai_summary"),
                item.get("operation"),
            ],
        )
    ).lower()
    return q in hay


def _sort_open(item: dict[str, Any]) -> tuple:
    prio = _PRIORITY_RANK_MAP.get(item.get("priority") or "", 4)
    due = item.get("due_date") or "9999-99-99"
    created = item.get("created_at") or ""
    return (prio, due, created)


def build_operations_live_payload(
    restaurant,
    *,
    current_user=None,
    limit: int = DEFAULT_LIMIT,
    query: str = "",
    search_by: str = "task",
    urgent_only: bool = False,
) -> dict[str, Any]:
    """Build the Operations Live feed payload (shared by UI + Miya agent)."""
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    search_by = (search_by or "task").strip().lower()
    if search_by not in {"staff", "task", "category"}:
        search_by = "task"
    query = (query or "").strip()

    today = timezone.now().date()
    future_cutoff = today + timedelta(days=30)
    completed_floor = today - timedelta(days=14)
    now = timezone.now()

    pending: list[dict[str, Any]] = []
    in_progress: list[dict[str, Any]] = []
    completed: list[dict[str, Any]] = []

    # Include custom-widget / process lanes (e.g. Wedding) - they used to be
    # excluded so Operations Live only showed the generic Tasks & Demands feed.
    db_base = (
        Task.objects.filter(restaurant=restaurant)
        .select_related(
            "assigned_to",
            "assigned_to__profile",
            "created_by",
            "created_by__profile",
            "custom_widget",
            "custom_widget__user",
        )
        .annotate(priority_rank=_PRIORITY_RANK)
    )

    for task in db_base.filter(status__in=["PENDING", "ACCEPTED"]).filter(
        Q(due_date__isnull=True) | Q(due_date__lte=future_cutoff)
    ).order_by("priority_rank", "due_date", "-created_at")[: limit * 4]:
        data = _serialize_dashboard_task(task, now=now)
        pending.append(_enrich_row(data, lane="pending", current_user=current_user, obj=task))

    for task in db_base.filter(status__in=["IN_PROGRESS", "UNABLE_TO_COMPLETE"]).order_by(
        "priority_rank", "-updated_at"
    )[: limit * 4]:
        data = _serialize_dashboard_task(task, now=now)
        in_progress.append(
            _enrich_row(data, lane="in_progress", current_user=current_user, obj=task)
        )

    for task in db_base.filter(
        status="COMPLETED", updated_at__date__gte=completed_floor
    ).order_by("-updated_at")[: limit * 4]:
        data = _serialize_dashboard_task(task, now=now)
        completed.append(
            _enrich_row(data, lane="completed", current_user=current_user, obj=task)
        )

    try:
        from staff.models import StaffRequest

        sr_qs = StaffRequest.objects.filter(restaurant=restaurant).select_related(
            "staff", "assignee"
        )

        for req in sr_qs.filter(status__in=_STAFF_PENDING).annotate(
            priority_rank=_PRIORITY_RANK
        ).order_by("priority_rank", "-created_at")[: limit * 4]:
            data = _serialize_staff_request(req, now=now)
            pending.append(
                _enrich_row(data, lane="pending", current_user=current_user, obj=req)
            )

        for req in sr_qs.filter(status__in=_STAFF_IN_PROGRESS).annotate(
            priority_rank=_PRIORITY_RANK
        ).order_by("priority_rank", "-updated_at")[: limit * 4]:
            data = _serialize_staff_request(req, now=now)
            in_progress.append(
                _enrich_row(data, lane="in_progress", current_user=current_user, obj=req)
            )

        for req in sr_qs.filter(
            status="CLOSED", updated_at__date__gte=completed_floor
        ).order_by("-updated_at")[: limit * 4]:
            data = _serialize_staff_request(req, now=now)
            completed.append(
                _enrich_row(data, lane="completed", current_user=current_user, obj=req)
            )
    except Exception:  # pragma: no cover
        pass

    try:
        from scheduling.task_templates import Task as SchedulingTask

        sched_base = (
            SchedulingTask.objects.filter(restaurant=restaurant, parent_task__isnull=True)
            .prefetch_related("assigned_to")
            .select_related("assigned_shift")
            .annotate(priority_rank=_PRIORITY_RANK)
        )

        for task in sched_base.filter(status="TODO").filter(
            Q(due_date__isnull=True) | Q(due_date__lte=future_cutoff)
        ).order_by("priority_rank", "due_date", "-created_at")[: limit * 3]:
            data = _serialize_scheduling_task(task)
            data["pill_status"] = "PENDING"
            data["age_label"] = _age_label(getattr(task, "created_at", None), now=now)
            pending.append(
                _enrich_row(data, lane="pending", current_user=current_user, obj=task)
            )

        for task in sched_base.filter(status="IN_PROGRESS").order_by(
            "priority_rank", "-updated_at"
        )[: limit * 3]:
            data = _serialize_scheduling_task(task)
            data["pill_status"] = "IN_PROGRESS"
            data["age_label"] = _age_label(getattr(task, "created_at", None), now=now)
            in_progress.append(
                _enrich_row(data, lane="in_progress", current_user=current_user, obj=task)
            )

        for task in sched_base.filter(
            status="COMPLETED", updated_at__date__gte=completed_floor
        ).order_by("-updated_at")[: limit * 3]:
            data = _serialize_scheduling_task(task)
            data["pill_status"] = "DONE"
            data["age_label"] = _age_label(getattr(task, "updated_at", None), now=now)
            completed.append(
                _enrich_row(data, lane="completed", current_user=current_user, obj=task)
            )
    except Exception:  # pragma: no cover
        pass

    try:
        from finance.models import Invoice

        inv_qs = Invoice.objects.filter(restaurant=restaurant).select_related(
            "created_by", "assigned_to"
        )
        for inv in inv_qs.filter(status=Invoice.STATUS_OPEN).order_by("-created_at")[
            : limit * 3
        ]:
            data = _serialize_invoice(inv, now=now)
            pending.append(
                _enrich_row(data, lane="pending", current_user=current_user, obj=inv)
            )
        for inv in inv_qs.filter(status=Invoice.STATUS_PAID).filter(
            updated_at__date__gte=completed_floor
        ).order_by("-updated_at")[: limit * 3]:
            data = _serialize_invoice(inv, now=now)
            completed.append(
                _enrich_row(data, lane="completed", current_user=current_user, obj=inv)
            )
    except Exception:  # pragma: no cover
        pass

    def apply_search(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        filtered = rows
        if query:
            filtered = [r for r in filtered if _matches_search(r, query, search_by)]
        if urgent_only:
            filtered = [
                r
                for r in filtered
                if (r.get("display_status") == "critical")
                or (r.get("priority") or "").upper() == "URGENT"
                or (r.get("pill_status") or "").upper() in {"ESCALATED", "OVERDUE"}
            ]
        return filtered

    pending = apply_search(pending)
    in_progress = apply_search(in_progress)
    completed = apply_search(completed)

    pending.sort(key=_sort_open)
    in_progress.sort(key=_sort_open)
    completed.sort(key=lambda x: x.get("updated_at") or "", reverse=True)

    return {
        "success": True,
        "restaurant_name": restaurant.name,
        "counts": {
            "pending": len(pending),
            "in_progress": len(in_progress),
            "completed": len(completed),
        },
        "pending": pending[:limit],
        "in_progress": in_progress[:limit],
        "completed": completed[:limit],
        "generated_at": now.isoformat(),
    }


def notify_managers_urgent(
    restaurant,
    *,
    message: str | None = None,
    task_id: str | None = None,
    channels: list[str] | None = None,
) -> dict[str, Any]:
    """Push an urgent Operations Live alert to managers (app + WhatsApp)."""
    from accounts.models import CustomUser
    from notifications.models import Notification
    from notifications.services import notification_service
    from staff.follow_up_helpers import normalize_phone

    channels = channels or ["app", "whatsapp"]
    feed = build_operations_live_payload(
        restaurant, limit=10, urgent_only=True
    )
    critical = (feed.get("pending") or []) + (feed.get("in_progress") or [])
    if task_id:
        critical = [r for r in critical if str(r.get("id")) == str(task_id)] or critical

    if message and message.strip():
        body = message.strip()
    elif critical:
        lines = []
        for row in critical[:5]:
            title = row.get("operation") or row.get("title") or "Item"
            cat = row.get("category") or "OPS"
            age = row.get("age_label") or ""
            lines.append(f"• [{cat}] {title}" + (f" ({age})" if age else ""))
        body = (
            f"⚠️ *Operations Live - {restaurant.name}*\n"
            f"{len(critical)} pressing item(s) need attention:\n"
            + "\n".join(lines)
            + "\nOpen Operations Live to triage."
        )
    else:
        body = (
            f"⚠️ *Operations Live - {restaurant.name}*\n"
            "Please check Operations Live for items that need your attention."
        )

    managers = CustomUser.objects.filter(
        restaurant=restaurant,
        role__in=["MANAGER", "ADMIN", "SUPER_ADMIN", "OWNER"],
        is_active=True,
    )
    app_sent = 0
    wa_sent = 0
    for manager in managers:
        if "app" in channels:
            try:
                notif = Notification.objects.create(
                    recipient=manager,
                    title="Operations Live - urgent",
                    message=body.replace("*", ""),
                    notification_type="SYSTEM_ALERT",
                    priority="URGENT",
                    data={
                        "route": "/dashboard/operations-live",
                        "task_id": task_id,
                        "count": len(critical),
                    },
                )
                notification_service.send_custom_notification(
                    recipient=manager,
                    notification=notif,
                    message=notif.message,
                    notification_type="SYSTEM_ALERT",
                    title=notif.title,
                    channels=["app", "push"],
                )
                app_sent += 1
            except Exception:
                pass
        if "whatsapp" in channels:
            phone = normalize_phone(getattr(manager, "phone", None))
            if phone:
                try:
                    ok, _ = notification_service.send_whatsapp_text(phone, body)
                    if ok:
                        wa_sent += 1
                except Exception:
                    pass

    return {
        "success": True,
        "managers_app": app_sent,
        "managers_whatsapp": wa_sent,
        "urgent_count": len(critical),
        "message_for_user": (
            f"Alerted {app_sent} manager(s) in-app"
            + (f" and {wa_sent} on WhatsApp" if wa_sent else "")
            + f" about {len(critical)} urgent item(s)."
        ),
    }


class OperationsLiveView(APIView):
    """
    GET /api/dashboard/operations-live/?limit=50&q=&search_by=staff|task|category

    Returns the unified operations feed for the Operations Live page.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        restaurant = getattr(request.user, "restaurant", None)
        if not restaurant:
            return Response(
                {"error": "No workspace associated"},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        try:
            limit = int(request.query_params.get("limit") or DEFAULT_LIMIT)
        except (TypeError, ValueError):
            limit = DEFAULT_LIMIT

        query = (request.query_params.get("q") or "").strip()
        search_by = (request.query_params.get("search_by") or "task").strip().lower()
        urgent_only = str(
            request.query_params.get("urgent_only") or ""
        ).lower() in {"1", "true", "yes"}

        data = build_operations_live_payload(
            restaurant,
            current_user=request.user,
            limit=limit,
            query=query,
            search_by=search_by,
            urgent_only=urgent_only,
        )

        return json_response_with_cache(
            request,
            data,
            max_age=15,
            private=True,
            stale_while_revalidate=30,
        )


@api_view(["GET", "POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_list_operations_live(request):
    """
    GET|POST /api/dashboard/agent/operations-live/

    Miya reads the Operations Live board (new / in progress / completed).
    """
    from scheduling.views_agent import _resolve_restaurant_for_agent

    restaurant, acting_user, err = _resolve_restaurant_for_agent(request)
    if err:
        return Response(
            {"success": False, "error": err["error"]},
            status=err["status"],
        )

    data = request.data if isinstance(getattr(request, "data", None), dict) else {}
    params = request.query_params

    def _pick(*keys, default=None):
        for key in keys:
            if key in data and data.get(key) not in (None, ""):
                return data.get(key)
            if key in params and params.get(key) not in (None, ""):
                return params.get(key)
        return default

    try:
        limit = int(_pick("limit", default=DEFAULT_LIMIT) or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT

    urgent_raw = _pick("urgent_only", "urgentOnly", "urgent", default=False)
    urgent_only = (
        urgent_raw is True
        or str(urgent_raw).lower() in {"1", "true", "yes"}
    )

    payload = build_operations_live_payload(
        restaurant,
        current_user=acting_user,
        limit=limit,
        query=str(_pick("q", "query", "search", default="") or ""),
        search_by=str(_pick("search_by", "searchBy", default="task") or "task"),
        urgent_only=urgent_only,
    )
    counts = payload.get("counts") or {}
    payload["message_for_user"] = (
        f"Operations Live - {counts.get('pending', 0)} new, "
        f"{counts.get('in_progress', 0)} in progress, "
        f"{counts.get('completed', 0)} completed."
        + (
            f" {counts.get('pending', 0) + counts.get('in_progress', 0)} urgent."
            if urgent_only
            else ""
        )
    )
    return Response(payload)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_notify_manager_urgent(request):
    """
    POST /api/dashboard/agent/operations-live/notify/

    Alert managers about pressing Operations Live items (app + WhatsApp).
    """
    from scheduling.views_agent import _resolve_restaurant_for_agent

    restaurant, _acting_user, err = _resolve_restaurant_for_agent(request)
    if err:
        return Response(
            {"success": False, "error": err["error"]},
            status=err["status"],
        )

    data = request.data if isinstance(getattr(request, "data", None), dict) else {}
    message = data.get("message") or data.get("whatsapp_message")
    task_id = data.get("task_id") or data.get("taskId")
    channels = data.get("channels")
    if isinstance(channels, str):
        channels = [c.strip() for c in channels.split(",") if c.strip()]
    if not isinstance(channels, list) or not channels:
        channels = ["app", "whatsapp"]

    result = notify_managers_urgent(
        restaurant,
        message=str(message) if message else None,
        task_id=str(task_id) if task_id else None,
        channels=channels,
    )
    return Response(result)
