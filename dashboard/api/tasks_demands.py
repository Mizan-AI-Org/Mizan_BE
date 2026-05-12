"""
Tasks & Demands widget endpoint.

Returns a lightweight, pre-bucketed view of tasks for the "Today's top 5
tasks" dashboard card: pending / in-progress / completed lanes, each
capped at N, with the assignee info the widget needs to render an avatar
and status pill without a second round-trip.

Sources merged into the widget (so managers have one inbox):
- `dashboard.Task` — items created by Miya, WhatsApp intake, email
  ingestion, or manual entry from the widget itself.
- `scheduling.Task` — items created on `/dashboard/scheduling` (the Task
  Management kanban), including tasks attached to `AssignedShift`.

Design notes:
- Priority-ordered: URGENT / HIGH first so managers see what matters.
- We filter to "today, overdue, or up to 7 days out" for the open lanes
  so the widget doesn't fill up with stale backlog; the Scheduling page
  is the full backlog viewer. "Completed" lane is last-N regardless of
  date so managers can confirm recent wins.
- PATCH ../status/ is a thin convenience the widget calls for the inline
  "Mark in progress" / "Mark done" buttons — it routes to whichever
  model owns that UUID.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone
from rest_framework import permissions, status as http_status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.http_caching import json_response_with_cache

from ..models import Task
from ..serializers import DashboardTaskCompactSerializer

logger = logging.getLogger(__name__)


# Alphabetical ordering on priority would put HIGH before URGENT (wrong).
# This annotation gives us the semantic ordering we actually want so the
# widget shows URGENT first, then HIGH, MEDIUM, LOW.
_PRIORITY_RANK = Case(
    When(priority="URGENT", then=Value(0)),
    When(priority="HIGH", then=Value(1)),
    When(priority="MEDIUM", then=Value(2)),
    When(priority="LOW", then=Value(3)),
    default=Value(4),
    output_field=IntegerField(),
)
_PRIORITY_RANK_MAP = {"URGENT": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


# `scheduling.Task.status` uses TODO, `dashboard.Task.status` uses PENDING.
# The widget/API vocabulary is PENDING/IN_PROGRESS/COMPLETED/CANCELLED.
_SCHED_STATUS_TO_WIDGET = {
    "TODO": "PENDING",
    "IN_PROGRESS": "IN_PROGRESS",
    "COMPLETED": "COMPLETED",
    "CANCELLED": "CANCELLED",
}
_WIDGET_STATUS_TO_SCHED = {v: k for k, v in _SCHED_STATUS_TO_WIDGET.items()}


ALLOWED_STATUS = {"PENDING", "IN_PROGRESS", "COMPLETED", "CANCELLED"}
DEFAULT_LIMIT = 5
MAX_LIMIT = 25


def _assignee_payload(user) -> dict | None:
    if not user:
        return None
    first = (getattr(user, "first_name", None) or "").strip()
    last = (getattr(user, "last_name", None) or "").strip()
    full = (f"{first} {last}").strip() or (getattr(user, "email", None) or "")
    initials = (first[:1] + last[:1]).upper() or (full[:2] if full else "").upper()
    return {
        "id": str(user.pk),
        "name": full,
        "initials": initials or "?",
        "role": getattr(user, "role", None),
    }


def _serialize_scheduling_task(task) -> dict[str, Any]:
    """Normalize a `scheduling.Task` row into the widget's compact shape.

    Picks the first assignee for the avatar (the kanban allows multiple
    but the widget row has space for one chip + a "+N" affordance which
    we don't currently render). Tags provenance so the widget shows a
    "Shift" / "Scheduling" chip next to the title.
    """
    assignee_user = task.assigned_to.all().first() if task.pk else None

    shift = getattr(task, "assigned_shift", None)
    if shift is not None:
        shift_date = getattr(shift, "shift_date", None)
        source_label = (
            f"Shift · {shift_date.strftime('%a %b %d')}"
            if shift_date
            else "Shift"
        )
    else:
        source_label = "Scheduling"

    return {
        "id": str(task.id),
        "title": task.title,
        "description": task.description or "",
        "priority": task.priority,
        "status": _SCHED_STATUS_TO_WIDGET.get(task.status, task.status),
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "source": "SYSTEM",
        "source_label": source_label,
        "ai_summary": "",
        "assignee": _assignee_payload(assignee_user),
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        # Lets the PATCH endpoint route back to the right model without a
        # second lookup; the frontend just echoes this back.
        "kind": "scheduling",
    }


def _serialize_dashboard_task(task) -> dict[str, Any]:
    data = DashboardTaskCompactSerializer(task).data
    data["kind"] = "dashboard"
    return data


def _notify_dashboard_task_new_assignee(
    *,
    task: Task,
    new_assignee,
    acting_user,
    note: str,
    had_previous_assignee: bool,
    whatsapp_override: str | None = None,
    send_whatsapp: bool = True,
) -> dict[str, Any]:
    """
    In-app bell + WhatsApp for a dashboard.Task assignee change (initial assign
    from nobody, or reassignment). Best-effort; never raises.
    """
    wa_result: dict[str, Any] = {
        "sent": False,
        "skipped_reason": None,
        "error": None,
        "provider_status": None,
    }
    try:
        from notifications.services import notification_service

        if acting_user is not None:
            mgr = acting_user
            sender_display = (
                f"{(mgr.first_name or '').strip()} {(mgr.last_name or '').strip()}".strip()
                or getattr(mgr, "email", None)
                or "Your manager"
            )
        else:
            sender_display = "Miya"

        try:
            notification_service.send_custom_notification(
                recipient=new_assignee,
                message=(
                    ("Task reassigned to you: " if had_previous_assignee else "New task: ")
                    + task.title
                    + (
                        f" (due {task.due_date.isoformat()})"
                        if task.due_date
                        else ""
                    )
                ),
                title="Task reassigned to you" if had_previous_assignee else "New task assigned",
                notification_type="TASK_ASSIGNED",
                channels=["app", "push"],
                sender=acting_user,
            )
        except Exception:
            logger.exception(
                "tasks_demands: in-app notify failed for task=%s assignee=%s",
                task.id,
                getattr(new_assignee, "id", None),
            )

        if not send_whatsapp:
            wa_result["skipped_reason"] = "disabled"
            return wa_result

        phone = (getattr(new_assignee, "phone", None) or "").strip()
        if not phone:
            wa_result["skipped_reason"] = "no_phone"
            wa_result["error"] = (
                f"{new_assignee.first_name or 'Staff member'} has no phone number on file."
            )
            return wa_result

        if whatsapp_override and str(whatsapp_override).strip():
            body = str(whatsapp_override).strip()
            if note:
                body = f"{body}\n\nNote: {note}"
        else:
            from dashboard.views_agent import _build_whatsapp_body

            body = _build_whatsapp_body(
                task=task,
                sender_name=sender_display,
                assignee_first_name=(new_assignee.first_name or "").strip(),
                override=None,
            )
            if had_previous_assignee:
                body = body.replace("New task from", "Task reassigned to you from", 1)
            if note:
                body = f"{body}\n\nNote from manager: {note}"

        ok, resp = notification_service.send_whatsapp_text(phone, body)
        wa_result["sent"] = bool(ok)
        if isinstance(resp, dict):
            wa_result["provider_status"] = resp.get("status_code")
            if not ok:
                wa_result["error"] = (resp.get("error") or "WhatsApp send failed")[:300]
    except Exception as exc:
        logger.exception("tasks_demands: WhatsApp notify failed for task=%s", task.id)
        wa_result["error"] = str(exc)[:200]
    return wa_result


def _sort_key(item: dict[str, Any]) -> tuple:
    """Order open lanes: priority → due_date (nulls last) → -created_at."""
    prio = _PRIORITY_RANK_MAP.get(item.get("priority") or "", 4)
    due = item.get("due_date") or "9999-99-99"
    created = item.get("created_at") or ""
    # negate created_at by using its reversed ordering via tuple with
    # inverted sort — we sort ascending on priority/due, descending on
    # created. Python tuples can't mix, so sort twice: first by created
    # desc implicitly via stable sort.
    return (prio, due, -len(created), created)


class TasksDemandsView(APIView):
    """
    GET /api/dashboard/tasks-demands/?limit=5

    Returns:
        {
          "counts": {"pending": N, "in_progress": N, "completed": N},
          "pending":     [DashboardTaskDemandItem, ...],
          "in_progress": [DashboardTaskDemandItem, ...],
          "completed":   [DashboardTaskDemandItem, ...],
          "generated_at": "...",
        }
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
        limit = max(1, min(limit, MAX_LIMIT))

        today = timezone.now().date()
        # Horizon for "open" items: anything due today, overdue, or up to
        # 7 days out. Anything further is hidden from the widget — it's a
        # today-focused card, not a backlog viewer.
        future_cutoff = today + timedelta(days=7)
        completed_floor = today - timedelta(days=7)

        # --- dashboard.Task -------------------------------------------------
        db_base = (
            Task.objects.filter(restaurant=restaurant)
            .select_related("assigned_to", "assigned_to__profile")
            .annotate(priority_rank=_PRIORITY_RANK)
        )

        db_pending = list(
            db_base.filter(status="PENDING")
            .filter(Q(due_date__isnull=True) | Q(due_date__lte=future_cutoff))
            .order_by("priority_rank", "due_date", "-created_at")[: limit * 2]
        )
        db_in_progress = list(
            db_base.filter(status="IN_PROGRESS")
            .order_by("priority_rank", "-updated_at")[: limit * 2]
        )
        db_completed = list(
            db_base.filter(status="COMPLETED")
            .order_by("-updated_at")[: limit * 2]
        )

        db_pending_count = db_base.filter(status="PENDING").count()
        db_in_progress_count = db_base.filter(status="IN_PROGRESS").count()
        db_completed_count = db_base.filter(
            status="COMPLETED", updated_at__date__gte=completed_floor
        ).count()

        # --- scheduling.Task (includes tasks on AssignedShifts) ------------
        # Imported lazily so dashboard still works if scheduling app ever
        # ships disabled in a test environment.
        sched_pending: list = []
        sched_in_progress: list = []
        sched_completed: list = []
        sched_pending_count = 0
        sched_in_progress_count = 0
        sched_completed_count = 0
        try:
            from scheduling.task_templates import Task as SchedulingTask

            sched_base = (
                SchedulingTask.objects.filter(restaurant=restaurant)
                # Root-level tasks only; subtasks are shown in the
                # scheduling page's detail view, not the dashboard.
                .filter(parent_task__isnull=True)
                .prefetch_related("assigned_to")
                .select_related("assigned_shift")
                .annotate(priority_rank=_PRIORITY_RANK)
            )

            sched_pending = list(
                sched_base.filter(status="TODO")
                .filter(Q(due_date__isnull=True) | Q(due_date__lte=future_cutoff))
                .order_by("priority_rank", "due_date", "-created_at")[: limit * 2]
            )
            sched_in_progress = list(
                sched_base.filter(status="IN_PROGRESS")
                .order_by("priority_rank", "-updated_at")[: limit * 2]
            )
            sched_completed = list(
                sched_base.filter(status="COMPLETED")
                .order_by("-updated_at")[: limit * 2]
            )
            sched_pending_count = sched_base.filter(status="TODO").count()
            sched_in_progress_count = sched_base.filter(status="IN_PROGRESS").count()
            sched_completed_count = sched_base.filter(
                status="COMPLETED", updated_at__date__gte=completed_floor
            ).count()
        except Exception:  # pragma: no cover - defensive
            # If the scheduling app or its migrations aren't present,
            # fall back to dashboard.Task only rather than 500-ing.
            pass

        # --- merge, stable-sort, slice -------------------------------------
        def merge(db_rows, sched_rows):
            merged = [_serialize_dashboard_task(t) for t in db_rows]
            merged.extend(_serialize_scheduling_task(t) for t in sched_rows)
            merged.sort(key=_sort_key)
            return merged[:limit]

        def merge_completed(db_rows, sched_rows):
            # Completed lane: sort by updated_at desc.
            merged = [_serialize_dashboard_task(t) for t in db_rows]
            merged.extend(_serialize_scheduling_task(t) for t in sched_rows)
            merged.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
            return merged[:limit]

        pending = merge(db_pending, sched_pending)
        in_progress = merge(db_in_progress, sched_in_progress)
        completed = merge_completed(db_completed, sched_completed)

        data = {
            "counts": {
                "pending": db_pending_count + sched_pending_count,
                "in_progress": db_in_progress_count + sched_in_progress_count,
                "completed": db_completed_count + sched_completed_count,
            },
            "pending": pending,
            "in_progress": in_progress,
            "completed": completed,
            "generated_at": timezone.now().isoformat(),
        }

        # Small, read-only, cheap-to-recompute — but the widget polls every
        # 60-90s from every open dashboard tab, so ETag short-circuits are
        # still very much worth it.
        return json_response_with_cache(
            request,
            data,
            max_age=30,
            private=True,
            stale_while_revalidate=60,
        )


# StaffRequest uses a richer status vocabulary than the widget. We map
# both directions so a manager who taps "Mark completed" on a WhatsApp-
# captured request closes it; tapping "Mark in progress" flips PENDING /
# ESCALATED rows to APPROVED ("manager has acknowledged & is on it").
# WAITING_ON is preserved when the source row is already in that state
# because the widget has no "waiting on" verb — managers set that via the
# Inbox detail page.
_WIDGET_STATUS_TO_STAFF_REQUEST = {
    "PENDING": "PENDING",
    "IN_PROGRESS": "APPROVED",
    "COMPLETED": "CLOSED",
    "CANCELLED": "REJECTED",
}


class TaskStatusUpdateView(APIView):
    """
    PATCH /api/dashboard/tasks-demands/<uuid>/status/
    Body: {"status": "PENDING" | "IN_PROGRESS" | "COMPLETED" | "CANCELLED"}

    Inline action for the widget's row menu. Looks up the row across
    every source the dashboard widgets surface — ``dashboard.Task``,
    ``scheduling.Task``, ``staff.StaffRequest``, ``finance.Invoice`` —
    and applies the status transition in whichever one owns the UUID.
    Any authenticated staff from the same tenant can flip status because
    the whole point of the widget is one-tap triage.

    Status transitions per source:

    - ``dashboard.Task`` / ``scheduling.Task``: pass-through (mapped to
      the scheduling TODO vocabulary for the latter).
    - ``staff.StaffRequest``: PENDING→PENDING, IN_PROGRESS→APPROVED,
      COMPLETED→CLOSED, CANCELLED→REJECTED. We don't blow away an
      existing WAITING_ON state when the manager is just trying to
      mark something done — the close still flows through.
    - ``finance.Invoice``: only COMPLETED (→PAID, via ``mark_paid``)
      and CANCELLED (→VOIDED) are accepted. PENDING / IN_PROGRESS
      have no meaning for an invoice and return 400.
    """

    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk=None):
        restaurant = getattr(request.user, "restaurant", None)
        if not restaurant:
            return Response(
                {"error": "No workspace associated"},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        new_status = (request.data or {}).get("status")
        if new_status not in ALLOWED_STATUS:
            return Response(
                {"error": f"status must be one of {sorted(ALLOWED_STATUS)}"},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 1) dashboard.Task
        try:
            task = Task.objects.select_related(
                "assigned_to", "assigned_to__profile"
            ).get(pk=pk, restaurant=restaurant)
        except Task.DoesNotExist:
            task = None

        if task is not None:
            task.status = new_status
            task.save(update_fields=["status", "updated_at"])
            return Response(_serialize_dashboard_task(task))

        # 2) scheduling.Task (TODO vocabulary)
        try:
            from scheduling.task_templates import Task as SchedulingTask

            try:
                sched = (
                    SchedulingTask.objects.prefetch_related("assigned_to")
                    .select_related("assigned_shift")
                    .get(pk=pk, restaurant=restaurant)
                )
            except SchedulingTask.DoesNotExist:
                sched = None
        except Exception:  # pragma: no cover - scheduling app missing
            sched = None

        if sched is not None:
            sched_status = _WIDGET_STATUS_TO_SCHED[new_status]
            update_fields = ["status", "updated_at"]
            sched.status = sched_status
            if sched_status == "COMPLETED":
                sched.completed_at = timezone.now()
                sched.completed_by = request.user
                sched.progress = 100
                update_fields += ["completed_at", "completed_by", "progress"]
            elif sched_status == "IN_PROGRESS" and (sched.progress or 0) == 0:
                sched.progress = 10
                update_fields += ["progress"]
            sched.save(update_fields=update_fields)
            return Response(_serialize_scheduling_task(sched))

        # 3) staff.StaffRequest — Miya / WhatsApp ingested rows. Without
        #    this branch every staff-request row in the dashboard widgets
        #    was effectively read-only: clicking "Mark completed" hit
        #    this view, fell through both blocks above, and returned 404.
        try:
            from staff.models import StaffRequest

            try:
                sr = (
                    StaffRequest.objects.select_related("staff", "assignee")
                    .get(pk=pk, restaurant=restaurant)
                )
            except StaffRequest.DoesNotExist:
                sr = None
        except Exception:  # pragma: no cover - staff app missing
            sr = None

        if sr is not None:
            target = _WIDGET_STATUS_TO_STAFF_REQUEST[new_status]
            sr.status = target
            sr.save(update_fields=["status", "updated_at"])
            # Best-effort: drop a system-comment on the request timeline
            # so /dashboard/staff-requests detail shows who closed it
            # and when — otherwise the widget action is invisible to
            # anyone reviewing the request's history.
            try:
                from staff.models import StaffRequestComment

                StaffRequestComment.objects.create(
                    request=sr,
                    author=request.user,
                    kind="status_change",
                    body=f"Status changed to {target} from the dashboard widget.",
                )
            except Exception:
                # Comment failure must never block the status flip.
                pass
            # Lazy import the granular serializer so older deployments
            # without the field still respond cleanly.
            from .category_tasks import _serialize_staff_request

            return Response(_serialize_staff_request(sr))

        # 4) finance.Invoice — manager flipping a Finance-widget row.
        #    Only COMPLETED (→ PAID) and CANCELLED (→ VOIDED) are valid;
        #    PENDING / IN_PROGRESS aren't meaningful for an invoice and
        #    we reject them with a clear message so the UI doesn't show
        #    a misleading "saved" toast.
        try:
            from finance.models import Invoice

            try:
                inv = Invoice.objects.select_related("created_by").get(
                    pk=pk, restaurant=restaurant
                )
            except Invoice.DoesNotExist:
                inv = None
        except Exception:  # pragma: no cover - finance app missing
            inv = None

        if inv is not None:
            if new_status == "COMPLETED":
                # Idempotent — ``mark_paid`` no-ops if the invoice is
                # already PAID, but bumping it again would overwrite
                # ``paid_at`` so we guard explicitly.
                if inv.status != Invoice.STATUS_PAID:
                    inv.mark_paid(user=request.user)
            elif new_status == "CANCELLED":
                if inv.status != Invoice.STATUS_VOIDED:
                    inv.status = Invoice.STATUS_VOIDED
                    inv.save(update_fields=["status", "updated_at"])
            else:
                return Response(
                    {
                        "error": (
                            "Invoices only accept COMPLETED (mark paid) or "
                            "CANCELLED (mark voided)."
                        )
                    },
                    status=http_status.HTTP_400_BAD_REQUEST,
                )
            from .category_tasks import _serialize_invoice

            return Response(_serialize_invoice(inv))

        return Response(
            {"error": "Task not found"}, status=http_status.HTTP_404_NOT_FOUND
        )


# Drag-and-drop endpoint vocabulary. The frontend uses a small set of
# bucket slugs that mirror the dashboard widget grid; this map turns a
# dropped-into bucket into the StaffRequest.category that should now
# own the row. ``urgent`` is a priority bump (no category change) and
# is handled separately.
_BUCKET_TO_STAFF_REQUEST_CATEGORY = {
    "human_resources": "HR",
    "finance": "FINANCE",
    "maintenance": "MAINTENANCE",
    "purchase_orders": "PURCHASE_ORDER",
    "miscellaneous": "OTHER",
}

ALLOWED_BUCKETS = frozenset({"urgent", *_BUCKET_TO_STAFF_REQUEST_CATEGORY.keys()})


class TaskBucketUpdateView(APIView):
    """
    PATCH /api/dashboard/tasks-demands/<uuid>/bucket/
    Body: ``{"bucket": "human_resources" | "finance" | "maintenance" |
                       "purchase_orders" | "miscellaneous" | "urgent"}``

    Drag-and-drop endpoint: when a manager drags a row from one
    dashboard category widget onto another, the FE calls this with the
    destination bucket. We dispatch by source model:

    - ``staff.StaffRequest``: change ``category`` to match the bucket
      (or bump ``priority`` to URGENT when dropped on the urgent
      widget). A ``status_change`` comment captures the move on the
      request timeline so it's auditable from the inbox detail page.
      If the row had no manual assignee (auto-routed by the inbox or
      tag-based fallback), we re-resolve the assignee against the new
      category so the right person picks it up.
    - ``finance.Invoice``: invoices live exclusively in the finance
      widget; dropping them anywhere else returns a friendly 400 with
      a hint to use Mark Paid / Mark Voided instead.
    - ``dashboard.Task`` and ``scheduling.Task``: rejected with a 400
      for now — these have their own category systems and need the
      regular edit dialog. We can wire them up later if managers ask
      for it; rejecting is safer than guessing the wrong target.
    """

    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk=None):
        restaurant = getattr(request.user, "restaurant", None)
        if not restaurant:
            return Response(
                {"error": "No workspace associated"},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        bucket = str((request.data or {}).get("bucket") or "").lower().strip()
        if bucket not in ALLOWED_BUCKETS:
            return Response(
                {"error": f"bucket must be one of {sorted(ALLOWED_BUCKETS)}"},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 1) staff.StaffRequest — the bulk of dashboard widget rows.
        try:
            from staff.models import StaffRequest

            try:
                sr = (
                    StaffRequest.objects.select_related("staff", "assignee")
                    .get(pk=pk, restaurant=restaurant)
                )
            except StaffRequest.DoesNotExist:
                sr = None
        except Exception:  # pragma: no cover - staff app missing
            sr = None

        if sr is not None:
            return self._move_staff_request(request, sr, bucket)

        # 2) finance.Invoice — anchored to the finance widget. Allow a
        #    no-op drop on finance, reject everything else.
        try:
            from finance.models import Invoice

            try:
                inv = Invoice.objects.select_related("created_by").get(
                    pk=pk, restaurant=restaurant
                )
            except Invoice.DoesNotExist:
                inv = None
        except Exception:  # pragma: no cover - finance app missing
            inv = None

        if inv is not None:
            if bucket == "finance":
                from .category_tasks import _serialize_invoice

                return Response(_serialize_invoice(inv))
            return Response(
                {
                    "error": (
                        "Invoices live in the Finance widget only. "
                        "Use 'Mark as Paid' or 'Mark as Voided' to clear "
                        "this row instead of moving it."
                    )
                },
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 3) dashboard.Task — rejected with a clear hint. These rows
        #    have a TaskCategory FK that doesn't map cleanly to the
        #    six widget buckets; the manager has to use the task edit
        #    dialog for now.
        try:
            task = Task.objects.get(pk=pk, restaurant=restaurant)
        except Task.DoesNotExist:
            task = None

        if task is not None:
            return Response(
                {
                    "error": (
                        "This task uses a custom category. Open it from "
                        "the task list to change its category."
                    )
                },
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        # 4) scheduling.Task — also rejected; scheduled tasks have
        #    their own list views.
        try:
            from scheduling.task_templates import Task as SchedulingTask

            try:
                sched = SchedulingTask.objects.get(pk=pk, restaurant=restaurant)
            except SchedulingTask.DoesNotExist:
                sched = None
        except Exception:  # pragma: no cover - scheduling app missing
            sched = None

        if sched is not None:
            return Response(
                {
                    "error": (
                        "Scheduled tasks live in their own list and "
                        "can't be re-bucketed from the dashboard."
                    )
                },
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {"error": "Item not found"}, status=http_status.HTTP_404_NOT_FOUND
        )

    # ------------------------------------------------------------------
    # StaffRequest mover — split out so the dispatcher above stays a
    # flat read; this method does the actual mutation + audit.
    # ------------------------------------------------------------------
    def _move_staff_request(self, request, sr, bucket):
        update_fields: list[str] = []
        comment_lines: list[str] = []

        if bucket == "urgent":
            # The urgent widget is a priority lane, not a category lane.
            # Bump priority and leave category alone so the manager
            # doesn't accidentally lose the original classification.
            if (sr.priority or "").upper() != "URGENT":
                old_priority = sr.priority or "MEDIUM"
                sr.priority = "URGENT"
                update_fields.append("priority")
                comment_lines.append(
                    f"Priority bumped from {old_priority} to URGENT via the dashboard."
                )
        else:
            target_category = _BUCKET_TO_STAFF_REQUEST_CATEGORY[bucket]
            if (sr.category or "").upper() != target_category:
                old_category = sr.category or "OTHER"
                sr.category = target_category
                update_fields.append("category")
                comment_lines.append(
                    f"Moved from {old_category} to {target_category} via the dashboard."
                )

                # Re-resolve the auto-assignee for the new category if
                # the row currently has no manual assignee. We only
                # rewrite the assignee when there isn't one — never
                # silently steal a row from a person the manager
                # explicitly placed it on.
                try:
                    if sr.assignee_id is None:
                        from staff.request_routing import (
                            resolve_default_assignee_for_category,
                        )

                        new_owner = resolve_default_assignee_for_category(
                            sr.restaurant, target_category
                        )
                        if new_owner is not None:
                            sr.assignee = new_owner
                            update_fields.append("assignee")
                            comment_lines.append(
                                f"Auto-assigned to {new_owner.first_name} {new_owner.last_name} "
                                f"based on the new category."
                            )
                except Exception:
                    # Routing must never block the bucket move itself.
                    pass

        if update_fields:
            update_fields.append("updated_at")
            sr.save(update_fields=update_fields)
            try:
                from staff.models import StaffRequestComment

                StaffRequestComment.objects.create(
                    request=sr,
                    author=request.user,
                    kind="status_change",
                    body=" ".join(comment_lines) if comment_lines else "Row moved via the dashboard.",
                )
            except Exception:
                pass

        from .category_tasks import _serialize_staff_request

        return Response(_serialize_staff_request(sr))


class TaskAssigneeUpdateView(APIView):
    """
    PATCH /api/dashboard/tasks-demands/<uuid>/assignee/
    Body: ``{"assignee_id": "<user-uuid>" | null, "note": "<optional>"}``

    Unified reassign endpoint for the dashboard widget rows. Mirrors
    ``TaskBucketUpdateView`` — dispatches across the four sources:

    - ``staff.StaffRequest``: writes ``assignee`` + drops a
      reassignment comment on the timeline (and a system entry when
      ``assignee_id`` is null = unassign). Reuses the same audit
      pattern the inbox detail page uses so the timeline stays the
      single source of truth for who owns what.
    - ``dashboard.Task``: writes ``assigned_to`` (single user; the
      legacy field is a FK so we don't try to fan out).
    - ``scheduling.Task``: replaces the M2M ``assigned_to`` set with
      a single user — scheduling tasks support multiple owners but
      the dashboard widget only renders one chip, so a reassign from
      the widget is a "set primary owner" action.
    - ``finance.Invoice``: invoices don't carry an assignee; we
      politely 400 with a hint pointing to the request inbox.

    Permissions match the rest of the widget endpoints: any
    authenticated tenant member can reassign because the row is
    already scoped to their restaurant by the dispatcher.
    """

    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk=None):
        restaurant = getattr(request.user, "restaurant", None)
        if not restaurant:
            return Response(
                {"error": "No workspace associated"},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        raw_assignee = (request.data or {}).get("assignee_id")
        # ``null`` / empty string is a deliberate "unassign" — supported
        # for StaffRequest and dashboard.Task. Scheduling.Task with
        # empty list is also valid (= no owner).
        new_user = None
        if raw_assignee:
            from accounts.models import CustomUser

            try:
                new_user = CustomUser.objects.get(
                    pk=raw_assignee, is_active=True,
                )
            except CustomUser.DoesNotExist:
                return Response(
                    {"error": "Assignee not found or inactive."},
                    status=http_status.HTTP_404_NOT_FOUND,
                )

            # Cross-tenant guard — never let a manager hand a row to
            # someone who isn't on their team. The check mirrors the
            # OR used by the staff list view (primary FK or active
            # ``StaffRestaurantLink``) so multi-restaurant teammates
            # remain valid targets.
            from django.db.models import Q
            from accounts.models import StaffRestaurantLink

            same_tenant = (
                new_user.restaurant_id == restaurant.id
                or StaffRestaurantLink.objects.filter(
                    user=new_user,
                    restaurant=restaurant,
                    is_active=True,
                ).exists()
            )
            if not same_tenant:
                return Response(
                    {"error": "That person isn't part of your team."},
                    status=http_status.HTTP_400_BAD_REQUEST,
                )

        note = str((request.data or {}).get("note") or "").strip()

        # 1) staff.StaffRequest
        try:
            from staff.models import StaffRequest

            try:
                sr = (
                    StaffRequest.objects.select_related("staff", "assignee")
                    .get(pk=pk, restaurant=restaurant)
                )
            except StaffRequest.DoesNotExist:
                sr = None
        except Exception:  # pragma: no cover - staff app missing
            sr = None

        if sr is not None:
            old = sr.assignee
            sr.assignee = new_user
            sr.save(update_fields=["assignee", "updated_at"])
            try:
                from staff.models import StaffRequestComment

                if new_user is None:
                    body = "Unassigned via the dashboard."
                elif old is None:
                    body = (
                        f"Assigned to {new_user.first_name} {new_user.last_name} "
                        f"via the dashboard."
                    )
                else:
                    body = (
                        f"Reassigned from {old.first_name} {old.last_name} "
                        f"to {new_user.first_name} {new_user.last_name} "
                        f"via the dashboard."
                    )
                if note:
                    body = f"{body} Note: {note}"
                StaffRequestComment.objects.create(
                    request=sr,
                    author=request.user,
                    kind="reassignment",
                    body=body,
                )
            except Exception:
                pass

            from .category_tasks import _serialize_staff_request

            return Response(_serialize_staff_request(sr))

        # 2) dashboard.Task
        try:
            task = Task.objects.select_related(
                "assigned_to", "assigned_to__profile"
            ).get(pk=pk, restaurant=restaurant)
        except Task.DoesNotExist:
            task = None

        if task is not None:
            old_assignee = task.assigned_to
            task.assigned_to = new_user
            task.save(update_fields=["assigned_to", "updated_at"])

            payload = _serialize_dashboard_task(task)
            if (
                new_user is not None
                and (old_assignee is None or new_user.id != old_assignee.id)
            ):
                payload["whatsapp"] = _notify_dashboard_task_new_assignee(
                    task=task,
                    new_assignee=new_user,
                    acting_user=request.user,
                    note=note,
                    had_previous_assignee=old_assignee is not None,
                )
            return Response(payload)

        # 3) scheduling.Task — M2M; widget reassign means "make this
        #    person the sole assignee" (the kanban can still fan it
        #    out manually).
        try:
            from scheduling.task_templates import Task as SchedulingTask

            try:
                sched = (
                    SchedulingTask.objects.prefetch_related("assigned_to")
                    .get(pk=pk, restaurant=restaurant)
                )
            except SchedulingTask.DoesNotExist:
                sched = None
        except Exception:  # pragma: no cover - scheduling app missing
            sched = None

        if sched is not None:
            if new_user is None:
                sched.assigned_to.clear()
            else:
                sched.assigned_to.set([new_user])
            sched.save(update_fields=["updated_at"])
            return Response(_serialize_scheduling_task(sched))

        # 4) finance.Invoice — no assignee field. Reject with a hint.
        try:
            from finance.models import Invoice

            try:
                inv = Invoice.objects.get(pk=pk, restaurant=restaurant)
            except Invoice.DoesNotExist:
                inv = None
        except Exception:  # pragma: no cover - finance app missing
            inv = None

        if inv is not None:
            return Response(
                {
                    "error": (
                        "Invoices don't have an owner. Move the request "
                        "into the inbox if you need to assign it to "
                        "someone."
                    )
                },
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {"error": "Item not found"}, status=http_status.HTTP_404_NOT_FOUND
        )
