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


class TaskStatusUpdateView(APIView):
    """
    PATCH /api/dashboard/tasks-demands/<uuid>/status/
    Body: {"status": "PENDING" | "IN_PROGRESS" | "COMPLETED" | "CANCELLED"}

    Inline action for the widget's row menu. Looks up the task in
    `dashboard.Task` first, then `scheduling.Task` (mapping status into
    the scheduling vocabulary). Any authenticated staff from the same
    tenant can flip status because the whole point of the widget is
    one-tap triage.
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

        if sched is None:
            return Response(
                {"error": "Task not found"}, status=http_status.HTTP_404_NOT_FOUND
            )

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
