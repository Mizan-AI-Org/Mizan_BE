"""Staff web inbox for dashboard.Task rows assigned to the logged-in user."""

from __future__ import annotations

from django.utils import timezone
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from core.http_caching import json_response_with_cache

from ..models import Task
from ..serializers import DashboardTaskCompactSerializer


class MyTasksView(APIView):
    """
    GET /api/dashboard/my-tasks/

    Returns open and recently completed dashboard.Task rows for the current user.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        restaurant = getattr(user, "restaurant", None)
        if not restaurant:
            return Response({"success": False, "error": "No workspace linked"}, status=400)

        status_filter = (request.query_params.get("status") or "open").lower()
        limit = min(int(request.query_params.get("limit") or 25), 50)

        qs = Task.objects.filter(restaurant=restaurant, assigned_to=user)
        if status_filter == "open":
            qs = qs.filter(status__in=("PENDING", "ACCEPTED", "IN_PROGRESS"))
        elif status_filter == "completed":
            qs = qs.filter(status="COMPLETED")
        elif status_filter != "all":
            qs = qs.filter(status=status_filter.upper())

        rows = qs.order_by("-updated_at")[:limit]
        now = timezone.now()
        payload = {
            "success": True,
            "count": len(rows),
            "tasks": [DashboardTaskCompactSerializer(t).data for t in rows],
            "generated_at": now.isoformat(),
        }
        return json_response_with_cache(request, payload, max_age=15)
