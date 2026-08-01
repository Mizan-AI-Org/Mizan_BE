"""
Manager-authenticated ops helpers used by the dashboard UI
(validation, global search, per-employee daily task progress).
"""
from __future__ import annotations

import logging

from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from dashboard.views_ops_memory import _is_user_absent

logger = logging.getLogger(__name__)

_MANAGER_ROLES = {"SUPER_ADMIN", "ADMIN", "MANAGER", "OWNER"}


def _restaurant_for(user):
    return getattr(user, "restaurant", None)


def _is_manager(user) -> bool:
    return (getattr(user, "role", "") or "").upper() in _MANAGER_ROLES


class ManagerValidateTaskView(APIView):
    """POST /api/dashboard/tasks/<uuid>/validate/"""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        if not _is_manager(request.user):
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        restaurant = _restaurant_for(request.user)
        if not restaurant:
            return Response({"detail": "No workspace"}, status=status.HTTP_400_BAD_REQUEST)

        from dashboard.models import Task

        task = Task.objects.filter(id=pk, restaurant=restaurant).first()
        if not task:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        task.requires_manager_validation = True
        task.manager_validated_at = timezone.now()
        task.manager_validated_by = request.user
        task.save(
            update_fields=[
                "requires_manager_validation",
                "manager_validated_at",
                "manager_validated_by",
                "updated_at",
            ]
        )
        return Response(
            {
                "success": True,
                "task_id": str(task.id),
                "manager_validated_at": task.manager_validated_at.isoformat(),
                "validation_label": "validated",
            }
        )


class ManagerValidateOrderView(APIView):
    """POST /api/dashboard/captured-orders/<uuid>/validate/"""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        if not _is_manager(request.user):
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        restaurant = _restaurant_for(request.user)
        if not restaurant:
            return Response({"detail": "No workspace"}, status=status.HTTP_400_BAD_REQUEST)

        from dashboard.models import StaffCapturedOrder

        order = StaffCapturedOrder.objects.filter(id=pk, restaurant=restaurant).first()
        if not order:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        order.requires_manager_validation = True
        order.manager_validated_at = timezone.now()
        order.save(
            update_fields=[
                "requires_manager_validation",
                "manager_validated_at",
                "updated_at",
            ]
        )
        return Response(
            {
                "success": True,
                "order_id": str(order.id),
                "manager_validated_at": order.manager_validated_at.isoformat(),
                "validation_label": "validated",
            }
        )


class ManagerRequireValidationView(APIView):
    """
    POST /api/dashboard/tasks/<uuid>/require-validation/
    Toggle requires_manager_validation on any task (cross-cutting).
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        if not _is_manager(request.user):
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        restaurant = _restaurant_for(request.user)
        if not restaurant:
            return Response({"detail": "No workspace"}, status=status.HTTP_400_BAD_REQUEST)

        from dashboard.models import Task

        task = Task.objects.filter(id=pk, restaurant=restaurant).first()
        if not task:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        data = request.data if isinstance(getattr(request, "data", None), dict) else {}
        required = data.get("required", True)
        if isinstance(required, str):
            required = required.lower() in ("1", "true", "yes")

        task.requires_manager_validation = bool(required)
        if not required:
            task.manager_validated_at = None
            task.manager_validated_by = None
        task.save(
            update_fields=[
                "requires_manager_validation",
                "manager_validated_at",
                "manager_validated_by",
                "updated_at",
            ]
        )
        return Response(
            {
                "success": True,
                "task_id": str(task.id),
                "requires_manager_validation": task.requires_manager_validation,
                "validation_label": (
                    None
                    if not task.requires_manager_validation
                    else (
                        "validated"
                        if task.manager_validated_at
                        else "not validated by manager"
                    )
                ),
            }
        )


class DashboardOpsSearchView(APIView):
    """GET /api/dashboard/ops-search/?q= — staff, tasks, requests, invoices, incidents, reminders."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        restaurant = _restaurant_for(request.user)
        if not restaurant:
            return Response({"detail": "No workspace"}, status=status.HTTP_400_BAD_REQUEST)

        q = (request.query_params.get("q") or "").strip()
        if len(q) < 2:
            return Response(
                {"detail": "q must be at least 2 characters"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from dashboard.ops_search_service import run_ops_search

        return Response(
            run_ops_search(
                restaurant,
                q=q,
                module=(request.query_params.get("module") or "all").strip().lower(),
                status_filter=(request.query_params.get("status") or "").strip(),
                category_filter=(request.query_params.get("category") or "").strip(),
                assignee_id=(request.query_params.get("assignee") or "").strip(),
                date_from=(request.query_params.get("date_from") or "").strip(),
                date_to=(request.query_params.get("date_to") or "").strip(),
                user=request.user,
            )
        )


class ManagerChaseRecordView(APIView):
    """POST /api/dashboard/records/chase/ — send WhatsApp follow-up for a task or request."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if not _is_manager(request.user):
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        restaurant = _restaurant_for(request.user)
        if not restaurant:
            return Response({"detail": "No workspace"}, status=status.HTTP_400_BAD_REQUEST)

        data = request.data if isinstance(getattr(request, "data", None), dict) else {}
        from rest_framework.test import APIRequestFactory
        from staff.views_agent import agent_chase_operational_record

        factory = APIRequestFactory()
        chase_req = factory.post(
            "/api/staff/agent/records/chase/",
            {
                "restaurant_id": str(restaurant.id),
                "record_id": data.get("record_id") or data.get("id"),
                "record_type": data.get("record_type") or data.get("kind"),
                "q": data.get("q") or data.get("query"),
            },
            format="json",
            HTTP_AUTHORIZATION=request.META.get("HTTP_AUTHORIZATION", ""),
            HTTP_X_RESTAURANT_ID=str(restaurant.id),
        )
        return agent_chase_operational_record(chase_req)


class StaffDailyTaskProgressView(APIView):
    """
    GET /api/dashboard/staff-daily-progress/
    Live progress for today, or archived snapshot for ?date=YYYY-MM-DD (managers).
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        restaurant = _restaurant_for(request.user)
        if not restaurant:
            return Response({"detail": "No workspace"}, status=status.HTTP_400_BAD_REQUEST)

        from datetime import datetime as dt

        from dashboard.services.staff_daily_progress import (
            compute_staff_daily_progress,
            load_staff_daily_progress_snapshot,
        )

        today = timezone.localdate()
        date_raw = (request.query_params.get("date") or "").strip()
        on_date = today
        archived = False

        if date_raw:
            try:
                on_date = dt.strptime(date_raw, "%Y-%m-%d").date()
            except ValueError:
                return Response(
                    {"detail": "Invalid date; use YYYY-MM-DD"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if on_date > today:
                return Response(
                    {"detail": "Cannot query future dates"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if on_date < today:
                if not _is_manager(request.user):
                    return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
                rows = load_staff_daily_progress_snapshot(restaurant, on_date)
                return Response(
                    {
                        "success": True,
                        "date": str(on_date),
                        "archived": True,
                        "staff": rows,
                    }
                )

        rows = compute_staff_daily_progress(restaurant, on_date=today)
        return Response(
            {
                "success": True,
                "date": str(today),
                "archived": archived,
                "staff": rows,
            }
        )


class StaffDailyProgressHistoryView(APIView):
    """GET /api/dashboard/staff-daily-progress/history/ — manager accountability archive index."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not _is_manager(request.user):
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        restaurant = _restaurant_for(request.user)
        if not restaurant:
            return Response({"detail": "No workspace"}, status=status.HTTP_400_BAD_REQUEST)

        from dashboard.services.staff_daily_progress import progress_history_summaries

        try:
            days = int(request.query_params.get("days") or 30)
        except (TypeError, ValueError):
            days = 30
        days = max(1, min(days, 90))

        return Response(
            {
                "success": True,
                "days": progress_history_summaries(restaurant, days=days),
            }
        )
