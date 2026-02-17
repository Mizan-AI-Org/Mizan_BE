"""
Recurring shift batch endpoints for dashboard (JWT).
Single-request create/delete for 7Shifts-style recurring behavior and faster save/load.
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status, permissions
from django.utils import timezone
from django.db import transaction
from datetime import datetime, timedelta
import uuid
import logging

from accounts.views import IsManagerOrAdmin
from accounts.models import CustomUser
from .models import AssignedShift, WeeklySchedule, ShiftTask
from .services import SchedulingService
from .task_templates import TaskTemplate

logger = logging.getLogger(__name__)


def _dates_for_days_of_week(start_date, end_date, days_of_week):
    """Yield dates where weekday() is in days_of_week (0=Mon, 6=Sun)."""
    allowed = set(int(x) for x in days_of_week if 0 <= int(x) <= 6)
    if not allowed:
        return
    current = start_date
    while current <= end_date:
        if current.weekday() in allowed:
            yield current
        current += timedelta(days=1)


def _dates_for_frequency(start_date, end_date, frequency):
    """Yield (date,) for each occurrence. start_date/end_date are date objects."""
    if frequency == "DAILY":
        current = start_date
        while current <= end_date:
            yield current
            current += timedelta(days=1)
    elif frequency == "WEEKLY":
        # Same weekday each week (e.g. every Monday)
        current = start_date
        while current <= end_date:
            yield current
            current += timedelta(days=7)
    elif frequency == "MONTHLY":
        # Same day-of-month each month
        from calendar import monthrange
        year, month, day = start_date.year, start_date.month, start_date.day
        current = start_date
        while current <= end_date:
            yield current
            if month == 12:
                year, month = year + 1, 1
            else:
                month += 1
            _, last = monthrange(year, month)
            next_day = min(day, last)
            current = datetime(year, month, next_day).date()
    else:
        # Default weekly
        current = start_date
        while current <= end_date:
            yield current
            current += timedelta(days=7)


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated, IsManagerOrAdmin])
def batch_create_recurring_shifts(request):
    """
    Create a full recurring series in one request (atomic). 7Shifts-style.
    Body: start_date (YYYY-MM-DD), end_date, frequency (DAILY|WEEKLY|MONTHLY),
    start_time (HH:MM or HH:MM:SS), end_time, staff_members (list of UUIDs),
    title (or notes), task_templates (list of UUIDs), tasks (list of {title, priority?}),
    color (optional).
    """
    user = request.user
    restaurant = getattr(user, "restaurant", None)
    if not restaurant:
        return Response(
            {"detail": "User has no restaurant."},
            status=status.HTTP_403_FORBIDDEN,
        )
    data = request.data or {}
    start_date_str = data.get("start_date")
    end_date_str = data.get("end_date")
    frequency = (data.get("frequency") or "WEEKLY").upper()
    if frequency not in ("DAILY", "WEEKLY", "MONTHLY", "CUSTOM"):
        frequency = "WEEKLY"
    days_of_week_raw = data.get("days_of_week") or data.get("daysOfWeek")
    if isinstance(days_of_week_raw, list) and len(days_of_week_raw) > 0:
        days_of_week = [int(x) for x in days_of_week_raw if x is not None and 0 <= int(x) <= 6]
    else:
        days_of_week = None
    start_time_str = data.get("start_time") or "09:00"
    end_time_str = data.get("end_time") or "17:00"
    staff_members_raw = data.get("staff_members") or []
    if not isinstance(staff_members_raw, list):
        staff_members_raw = [staff_members_raw] if staff_members_raw else []
    title = (data.get("title") or data.get("notes") or "").strip() or "Shift"
    task_templates_raw = data.get("task_templates") or []
    tasks_payload = data.get("tasks") or []
    color = data.get("color")

    if not all([start_date_str, end_date_str]):
        return Response(
            {"detail": "start_date and end_date are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not staff_members_raw:
        return Response(
            {"detail": "At least one staff member is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not task_templates_raw and not tasks_payload:
        return Response(
            {"detail": "At least one task template or custom task is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    except ValueError:
        return Response(
            {"detail": "Invalid start_date or end_date. Use YYYY-MM-DD."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if start_date > end_date:
        return Response(
            {"detail": "start_date must be on or before end_date."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        if len(start_time_str) == 5:
            start_time = datetime.strptime(start_time_str, "%H:%M").time()
        else:
            start_time = datetime.strptime(start_time_str, "%H:%M:%S").time()
        if len(end_time_str) == 5:
            end_time = datetime.strptime(end_time_str, "%H:%M").time()
        else:
            end_time = datetime.strptime(end_time_str, "%H:%M:%S").time()
    except ValueError:
        return Response(
            {"detail": "Invalid start_time or end_time. Use HH:MM or HH:MM:SS."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if end_time <= start_time:
        return Response(
            {"detail": "end_time must be after start_time."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    staff_ids = [str(x).strip() for x in staff_members_raw if x]
    staff_users = list(
        CustomUser.objects.filter(id__in=staff_ids, restaurant=restaurant)
    )
    if not staff_users:
        return Response(
            {"detail": "No valid staff members found in this restaurant."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    primary_staff = staff_users[0]
    role = getattr(primary_staff, "role", None) or "SERVER"

    recurrence_group_id = uuid.uuid4()
    task_template_ids = [str(x).strip() for x in task_templates_raw if x]
    templates = []
    if task_template_ids:
        templates = list(
            TaskTemplate.objects.filter(
                id__in=task_template_ids,
                restaurant=restaurant,
                is_active=True,
            )
        )

    if days_of_week:
        date_iter = _dates_for_days_of_week(start_date, end_date, days_of_week)
    else:
        date_iter = _dates_for_frequency(start_date, end_date, frequency)

    created_ids = []
    created_count = 0
    max_shifts = 365
    try:
        with transaction.atomic():
            for shift_date in date_iter:
                if created_count >= max_shifts:
                    break
                days_since_monday = shift_date.weekday()
                week_start = shift_date - timedelta(days=days_since_monday)
                week_end = week_start + timedelta(days=6)
                schedule, _ = WeeklySchedule.objects.get_or_create(
                    restaurant=restaurant,
                    week_start=week_start,
                    defaults={"week_end": week_end},
                )
                start_dt = timezone.datetime.combine(shift_date, start_time)
                end_dt = timezone.datetime.combine(shift_date, end_time)
                if timezone.is_naive(start_dt):
                    start_dt = timezone.make_aware(start_dt)
                if timezone.is_naive(end_dt):
                    end_dt = timezone.make_aware(end_dt)

                shift = AssignedShift.objects.create(
                    schedule=schedule,
                    staff=primary_staff,
                    shift_date=shift_date,
                    start_time=start_dt,
                    end_time=end_dt,
                    role=role.upper(),
                    notes=title,
                    status="SCHEDULED",
                    created_by=user,
                    last_modified_by=user,
                    is_recurring=True,
                    recurrence_group_id=recurrence_group_id,
                    color=color or "",
                )
                for s in staff_users:
                    shift.staff_members.add(s)
                if templates:
                    shift.task_templates.add(*templates)
                for t in tasks_payload:
                    if not isinstance(t, dict):
                        continue
                    t_title = (t.get("title") or t.get("name") or "").strip()
                    if not t_title:
                        continue
                    prio = (t.get("priority") or "MEDIUM").upper()
                    if prio not in ("LOW", "MEDIUM", "HIGH", "URGENT"):
                        prio = "MEDIUM"
                    ShiftTask.objects.create(
                        shift=shift,
                        title=t_title[:255],
                        priority=prio,
                        status="TODO",
                        assigned_to=primary_staff,
                        created_by=user,
                    )
                try:
                    SchedulingService.ensure_shift_color(shift)
                except Exception:
                    pass
                created_ids.append(str(shift.id))
                created_count += 1
    except Exception as e:
        logger.exception("Batch create recurring shifts failed")
        return Response(
            {"detail": str(e)[:200]},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Notify staff once per series (optional; can be heavy)
    if created_ids and created_count <= 50:
        try:
            first_shift = AssignedShift.objects.get(id=created_ids[0])
            SchedulingService.notify_shift_assignment(first_shift, force_whatsapp=True)
        except Exception as e:
            logger.warning("Recurring batch notify failed: %s", e)

    return Response(
        {
            "created": created_count,
            "recurrence_group_id": str(recurrence_group_id),
            "shift_ids": created_ids[:100],
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated, IsManagerOrAdmin])
def batch_delete_recurring_series(request):
    """
    Delete all shifts in a recurrence group in one request (atomic).
    Body: { "recurrence_group_id": "uuid" }.
    """
    user = request.user
    restaurant = getattr(user, "restaurant", None)
    if not restaurant:
        return Response(
            {"detail": "User has no restaurant."},
            status=status.HTTP_403_FORBIDDEN,
        )
    data = request.data or {}
    recurrence_group_id = data.get("recurrence_group_id")
    if not recurrence_group_id:
        return Response(
            {"detail": "recurrence_group_id is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        rid = uuid.UUID(str(recurrence_group_id))
    except ValueError:
        return Response(
            {"detail": "Invalid recurrence_group_id."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    with transaction.atomic():
        deleted_count, _ = AssignedShift.objects.filter(
            recurrence_group_id=rid,
            schedule__restaurant=restaurant,
        ).delete()
    return Response(
        {"deleted": deleted_count},
        status=status.HTTP_200_OK,
    )
