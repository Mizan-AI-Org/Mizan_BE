"""
Manager-authenticated ops helpers used by the dashboard UI
(validation, global search, per-employee daily task progress).
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.db.models import Q
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

        module = (request.query_params.get("module") or "all").strip().lower()
        status_filter = (request.query_params.get("status") or "").strip()
        category_filter = (request.query_params.get("category") or "").strip()
        assignee_id = (request.query_params.get("assignee") or "").strip()
        date_from = (request.query_params.get("date_from") or "").strip()
        date_to = (request.query_params.get("date_to") or "").strip()

        from accounts.models import CustomUser
        from dashboard.models import Task
        from staff.models import StaffRequest

        include = lambda key: module in ("", "all", key)

        staff_hits = []
        if include("staff"):
            staff_hits = list(
                CustomUser.objects.filter(restaurant=restaurant)
                .filter(
                    Q(first_name__icontains=q)
                    | Q(last_name__icontains=q)
                    | Q(email__icontains=q)
                    | Q(phone__icontains=q)
                )[:15]
            )

        tasks = []
        if include("tasks"):
            tqs = Task.objects.filter(restaurant=restaurant).filter(
                Q(title__icontains=q) | Q(description__icontains=q)
            )
            if status_filter:
                tqs = tqs.filter(status__iexact=status_filter)
            if category_filter:
                tqs = tqs.filter(category__iexact=category_filter)
            if assignee_id:
                tqs = tqs.filter(assigned_to_id=assignee_id)
            if date_from:
                tqs = tqs.filter(due_date__gte=date_from)
            if date_to:
                tqs = tqs.filter(due_date__lte=date_to)
            tasks = list(tqs.select_related("assigned_to")[:20])

        requests_hits = []
        if include("requests") or include("staff_requests"):
            rqs = StaffRequest.objects.filter(restaurant=restaurant).filter(
                Q(subject__icontains=q) | Q(description__icontains=q)
            )
            if status_filter:
                rqs = rqs.filter(status__iexact=status_filter)
            if category_filter:
                rqs = rqs.filter(category__iexact=category_filter)
            if assignee_id:
                rqs = rqs.filter(assignee_id=assignee_id)
            requests_hits = list(rqs.select_related("assignee", "staff")[:20])

        invoices = []
        if include("invoices"):
            try:
                from finance.models import Invoice

                iqs = Invoice.objects.filter(restaurant=restaurant).filter(
                    Q(vendor_name__icontains=q)
                    | Q(invoice_number__icontains=q)
                    | Q(notes__icontains=q)
                    | Q(category__icontains=q)
                )
                if status_filter:
                    iqs = iqs.filter(status__iexact=status_filter)
                if date_from:
                    iqs = iqs.filter(due_date__gte=date_from)
                if date_to:
                    iqs = iqs.filter(due_date__lte=date_to)
                invoices = list(iqs[:15])
            except Exception:
                logger.exception("ops-search invoices failed")

        incidents = []
        if include("incidents"):
            try:
                from staff.models import SafetyConcernReport

                incidents = list(
                    SafetyConcernReport.objects.filter(restaurant=restaurant)
                    .filter(
                        Q(title__icontains=q)
                        | Q(description__icontains=q)
                        | Q(location__icontains=q)
                        | Q(incident_type__icontains=q)
                    )
                    .order_by("-created_at")[:15]
                )
            except Exception:
                try:
                    from staff.models import IncidentReport

                    incidents = list(
                        IncidentReport.objects.filter(restaurant=restaurant)
                        .filter(Q(title__icontains=q) | Q(description__icontains=q))
                        .order_by("-created_at")[:15]
                    )
                except Exception:
                    logger.debug("ops-search: no incident model available", exc_info=True)

        reminders = []
        if include("reminders"):
            try:
                from scheduling.memory_models import PersonalReminder

                reminders = list(
                    PersonalReminder.objects.filter(restaurant=restaurant)
                    .filter(Q(title__icontains=q) | Q(body__icontains=q))
                    .select_related("owner")
                    .order_by("-due_at")[:15]
                )
            except Exception:
                logger.debug("ops-search: reminders unavailable", exc_info=True)

        meetings = []
        if include("meetings"):
            try:
                from django.utils import timezone as dj_tz
                from dashboard.api.meetings_reminders import (
                    MeetingsRemindersView,
                    _get_valid_access_token,
                )

                access_token, _gcal = _get_valid_access_token(restaurant)
                if access_token:
                    view = MeetingsRemindersView()
                    events = view._fetch_events(
                        access_token, request.user, dj_tz.now()
                    )
                    q_l = q.lower()
                    for e in events or []:
                        title = (e.get("title") or "")
                        if q_l in title.lower():
                            meetings.append(
                                {
                                    "id": e.get("id"),
                                    "title": title,
                                    "start": e.get("start"),
                                    "end": e.get("end"),
                                    "status": e.get("status"),
                                    "owner_label": e.get("owner_label"),
                                    "href": e.get("html_link"),
                                }
                            )
                            if len(meetings) >= 10:
                                break
            except Exception:
                logger.debug("ops-search: meetings unavailable", exc_info=True)

        staff_payload = []
        for u in staff_hits:
            assigned = list(
                Task.objects.filter(
                    restaurant=restaurant,
                    assigned_to=u,
                    status__in=["PENDING", "ACCEPTED", "IN_PROGRESS"],
                ).values("id", "title", "status", "priority")[:10]
            )
            staff_payload.append(
                {
                    "id": str(u.id),
                    "name": f"{u.first_name or ''} {u.last_name or ''}".strip() or u.email,
                    "phone": u.phone or "",
                    "role": getattr(u, "role", "") or "",
                    "is_absent": _is_user_absent(u, restaurant),
                    "open_tasks": [
                        {"id": str(t["id"]), "title": t["title"], "status": t["status"]}
                        for t in assigned
                    ],
                }
            )

        def _task_row(t: Task):
            absent = _is_user_absent(t.assigned_to, restaurant) if t.assigned_to_id else False
            return {
                "id": str(t.id),
                "title": t.title,
                "status": t.status,
                "category": t.category,
                "assigned_to": (
                    f"{t.assigned_to.first_name} {t.assigned_to.last_name}".strip()
                    if t.assigned_to_id
                    else None
                ),
                "assignee_absent": absent,
                "requires_manager_validation": t.requires_manager_validation,
                "validation_label": (
                    None
                    if not t.requires_manager_validation
                    else ("validated" if t.manager_validated_at else "not validated by manager")
                ),
                "has_photo_proof": bool(t.proof_media_url),
                "proof_media_url": t.proof_media_url or None,
                "href": f"/dashboard/staff-requests?list=dashboard&kind=dashboard&id={t.id}",
            }

        return Response(
            {
                "success": True,
                "module": module,
                "staff": staff_payload,
                "tasks": [_task_row(t) for t in tasks],
                "staff_requests": [
                    {
                        "id": str(r.id),
                        "subject": r.subject,
                        "category": r.category,
                        "status": r.status,
                        "assignee": (
                            f"{r.assignee.first_name} {r.assignee.last_name}".strip()
                            if r.assignee_id
                            else None
                        ),
                        "assignee_absent": _is_user_absent(r.assignee, restaurant)
                        if r.assignee_id
                        else False,
                        "href": f"/dashboard/staff-requests/{r.id}",
                    }
                    for r in requests_hits
                ],
                "invoices": [
                    {
                        "id": str(inv.id),
                        "vendor_name": inv.vendor_name,
                        "invoice_number": getattr(inv, "invoice_number", "") or "",
                        "amount": str(inv.amount),
                        "currency": getattr(inv, "currency", "") or "",
                        "status": inv.status,
                        "due_date": inv.due_date.isoformat() if inv.due_date else None,
                        "href": f"/dashboard/staff-requests?kind=invoice&id={inv.id}",
                    }
                    for inv in invoices
                ],
                "incidents": [
                    {
                        "id": str(inc.id),
                        "title": getattr(inc, "title", "") or "",
                        "status": getattr(inc, "status", "") or "",
                        "href": f"/dashboard/staff-requests?kind=incident&id={inc.id}",
                    }
                    for inc in incidents
                ],
                "reminders": [
                    {
                        "id": str(rem.id),
                        "title": rem.title,
                        "due_at": rem.due_at.isoformat() if rem.due_at else None,
                        "status": rem.status,
                        "has_attachment": bool(
                            getattr(rem, "attachment_url", None)
                            or getattr(rem, "attachment", None)
                        ),
                    }
                    for rem in reminders
                ],
                "meetings": meetings,
            }
        )


class StaffDailyTaskProgressView(APIView):
    """
    GET /api/dashboard/staff-daily-progress/
    Per-employee progress across today's dashboard tasks.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        restaurant = _restaurant_for(request.user)
        if not restaurant:
            return Response({"detail": "No workspace"}, status=status.HTTP_400_BAD_REQUEST)

        from accounts.models import CustomUser
        from dashboard.models import Task

        today = timezone.localdate()
        now = timezone.now()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)

        staff = list(
            CustomUser.objects.filter(
                restaurant=restaurant,
                is_active=True,
            ).order_by("first_name", "last_name")[:80]
        )

        rows = []
        for u in staff:
            today_qs = Task.objects.filter(
                restaurant=restaurant,
                assigned_to=u,
            ).filter(
                Q(created_at__gte=day_start, created_at__lt=day_end)
                | Q(due_date=today)
            )
            open_qs = Task.objects.filter(
                restaurant=restaurant,
                assigned_to=u,
                status__in=["PENDING", "ACCEPTED", "IN_PROGRESS"],
            )
            today_total = today_qs.count()
            open_count = open_qs.count()
            if today_total == 0 and open_count == 0:
                continue

            if today_total > 0:
                done = today_qs.filter(status="COMPLETED").count()
                total = today_total
            else:
                done = 0
                total = open_count

            pct = int(round((done / total) * 100)) if total else 0
            rows.append(
                {
                    "id": str(u.id),
                    "name": f"{u.first_name or ''} {u.last_name or ''}".strip() or u.email,
                    "role": getattr(u, "role", "") or "",
                    "is_absent": _is_user_absent(u, restaurant),
                    "total": total,
                    "done": done,
                    "open": open_count,
                    "pct": pct,
                }
            )

        rows.sort(key=lambda r: (-r["open"], r["name"].lower()))
        return Response({"success": True, "date": str(today), "staff": rows})
