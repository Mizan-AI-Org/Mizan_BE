"""
Tasks & Demands widget endpoint.

Returns a lightweight, pre-bucketed view of dashboard.Task rows for the
"Today's top 5 tasks" dashboard card: open / in-progress / completed
lanes, each capped at N, with the assignee info the widget needs to
render an avatar and status pill without a second round-trip.

Design notes:
- Separate from /dashboard/tasks-old/ (which is full CRUD on Task) and
  from the router-mounted /dashboard/tasks/ (which is ShiftTask). A
  dedicated read endpoint lets us keep the payload tiny and add an
  ETag/Cache-Control path without having to worry about write traffic.
- Priority-ordered: URGENT / HIGH first so managers see what matters.
- We filter to "today or undated" for the open/in-progress lanes so the
  widget doesn't fill up with stale month-old rows. "Completed" lane is
  last-N regardless of date so managers can confirm recent wins.
- PATCH ../status/ is a thin convenience that the widget calls for the
  inline "Mark in progress" / "Mark done" buttons in the row menu.
"""

from __future__ import annotations

from datetime import timedelta

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


ALLOWED_STATUS = {"PENDING", "IN_PROGRESS", "COMPLETED", "CANCELLED"}
DEFAULT_LIMIT = 5
MAX_LIMIT = 25


class TasksDemandsView(APIView):
    """
    GET /api/dashboard/tasks-demands/?limit=5

    Returns:
        {
          "counts": {"pending": N, "in_progress": N, "completed": N},
          "pending":     [DashboardTaskCompactSerializer, ...],
          "in_progress": [DashboardTaskCompactSerializer, ...],
          "completed":   [DashboardTaskCompactSerializer, ...],
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
        # Horizon for "open" items: anything due today, overdue, or undated.
        # Anything scheduled >7 days out is deliberately hidden from the
        # widget — it's a today-focused card, not a backlog viewer.
        future_cutoff = today + timedelta(days=7)

        base = (
            Task.objects
            .filter(restaurant=restaurant)
            .select_related("assigned_to", "assigned_to__profile")
            .annotate(priority_rank=_PRIORITY_RANK)
        )

        pending = list(
            base.filter(status="PENDING")
            .filter(Q(due_date__isnull=True) | Q(due_date__lte=future_cutoff))
            .order_by("priority_rank", "due_date", "-created_at")[:limit]
        )

        in_progress = list(
            base.filter(status="IN_PROGRESS")
            .order_by("priority_rank", "-updated_at")[:limit]
        )

        completed = list(
            base.filter(status="COMPLETED")
            .order_by("-updated_at")[:limit]
        )

        data = {
            "counts": {
                "pending": base.filter(status="PENDING").count(),
                "in_progress": base.filter(status="IN_PROGRESS").count(),
                "completed": base.filter(
                    status="COMPLETED",
                    updated_at__date__gte=today - timedelta(days=7),
                ).count(),
            },
            "pending": DashboardTaskCompactSerializer(pending, many=True).data,
            "in_progress": DashboardTaskCompactSerializer(in_progress, many=True).data,
            "completed": DashboardTaskCompactSerializer(completed, many=True).data,
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

    Inline action for the widget's row menu. Any authenticated staff from
    the same tenant can flip status; we don't require admin for this
    because the whole point of the widget is one-tap triage.
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
        try:
            task = Task.objects.select_related(
                "assigned_to", "assigned_to__profile"
            ).get(pk=pk, restaurant=restaurant)
        except Task.DoesNotExist:
            return Response(
                {"error": "Task not found"}, status=http_status.HTTP_404_NOT_FOUND
            )
        task.status = new_status
        task.save(update_fields=["status", "updated_at"])
        return Response(DashboardTaskCompactSerializer(task).data)
