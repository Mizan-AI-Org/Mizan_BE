"""GET /api/dashboard/custom-widgets/<uuid>/tasks/ — tasks for one custom tile."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone
from rest_framework import permissions, status as http_status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.http_caching import json_response_with_cache

from ..api.category_tasks import (
    _PRIORITY_RANK,
    _serialize_dashboard_task,
)
from ..models import DashboardCustomWidget, Task

DEFAULT_LIMIT = 5
MAX_LIMIT = 25


class CustomWidgetTasksView(APIView):
    """Return open / in-progress / recently completed tasks for a custom tile."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk=None):
        restaurant = getattr(request.user, "restaurant", None)
        if not restaurant:
            return Response(
                {"error": "No workspace associated"},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        widget = DashboardCustomWidget.objects.filter(
            pk=pk,
            user=request.user,
            restaurant=restaurant,
        ).first()
        if widget is None:
            return Response({"error": "Widget not found"}, status=http_status.HTTP_404_NOT_FOUND)

        try:
            limit = int(request.query_params.get("limit") or DEFAULT_LIMIT)
        except (TypeError, ValueError):
            limit = DEFAULT_LIMIT
        limit = max(1, min(limit, MAX_LIMIT))

        today = timezone.now().date()
        future_cutoff = today + timedelta(days=14)
        completed_floor = today - timedelta(days=14)
        serialize_now = timezone.now()

        base = (
            Task.objects.filter(
                restaurant=restaurant,
                custom_widget=widget,
            )
            .select_related("assigned_to")
            .annotate(priority_rank=_PRIORITY_RANK)
        )

        open_qs = base.filter(status__in=("PENDING", "IN_PROGRESS")).filter(
            Q(due_date__isnull=True) | Q(due_date__lte=future_cutoff)
        )
        completed_qs = base.filter(
            status="COMPLETED",
            updated_at__date__gte=completed_floor,
        )

        open_rows = list(
            open_qs.order_by("priority_rank", "due_date", "-created_at")[: limit * 3]
        )
        completed_rows = list(
            completed_qs.order_by("-updated_at")[: limit * 3]
        )

        open_items: list[dict[str, Any]] = []
        for row in open_rows:
            open_items.append(_serialize_dashboard_task(row, now=serialize_now))

        completed_items: list[dict[str, Any]] = []
        for row in completed_rows:
            completed_items.append(_serialize_dashboard_task(row, now=serialize_now))

        pending_items = [x for x in open_items if x.get("status") == "PENDING"]
        in_progress_items = [x for x in open_items if x.get("status") == "IN_PROGRESS"]

        def _trim(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return rows[:limit]

        pending_items = _trim(pending_items)
        in_progress_items = _trim(in_progress_items)
        completed_items = _trim(completed_items)

        open_count = open_qs.count()
        data = {
            "widget_id": widget.slot_id(),
            "title": widget.title,
            "items": pending_items + in_progress_items,
            "pending": pending_items,
            "in_progress": in_progress_items,
            "completed": completed_items,
            "counts": {
                "open": open_count,
                "in_progress": open_qs.filter(status="IN_PROGRESS").count(),
                "completed": completed_qs.count(),
                "pending": open_qs.filter(status="PENDING").count(),
            },
            "generated_at": serialize_now.isoformat(),
        }
        return json_response_with_cache(
            request,
            data,
            max_age=30,
            private=True,
            stale_while_revalidate=60,
        )
