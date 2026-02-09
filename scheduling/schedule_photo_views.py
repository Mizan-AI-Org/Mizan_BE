"""
Views for schedule photo upload: parse image and apply as template or to a week.
"""
import logging
import re
from datetime import datetime, timedelta, time as _time
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from accounts.views import IsManagerOrAdmin
from accounts.models import CustomUser
from .models import ScheduleTemplate, TemplateShift, WeeklySchedule, AssignedShift
from .schedule_photo_service import parse_schedule_image
from .schedule_document_service import parse_schedule_document as parse_schedule_document_service

logger = logging.getLogger(__name__)


def _get_restaurant_staff(restaurant):
    """Staff in this restaurant (has role, not necessarily is_staff)."""
    return CustomUser.objects.filter(restaurant=restaurant).exclude(role__isnull=True).exclude(role="")


def _match_employee_name_to_staff(name: str, restaurant) -> CustomUser | None:
    """Fuzzy match employee name to a CustomUser in the restaurant."""
    if not name or not restaurant:
        return None
    name = (name or "").strip()
    if not name:
        return None
    staff = _get_restaurant_staff(restaurant)
    name_lower = name.lower()
    # "John Doe" or "Doe, John"
    parts = [p.strip() for p in re.split(r"[\s,]+", name) if p.strip()]
    for u in staff:
        first = (u.first_name or "").lower()
        last = (u.last_name or "").lower()
        full = f"{first} {last}".strip()
        rev = f"{last} {first}".strip()
        if name_lower == full or name_lower == rev:
            return u
        if first and name_lower == first:
            return u
        if last and name_lower == last:
            return u
        if parts and first and last:
            if (parts[0] == first and (len(parts) == 1 or parts[-1] == last)):
                return u
            if (parts[0] == last and len(parts) > 1 and parts[1] == first):
                return u
    return None


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsManagerOrAdmin])
def parse_schedule_photo(request):
    """
    POST multipart: `photo` (file) or `image` (file).
    Returns parsed schedule: template_name, shifts (employee_name, role, department, day_of_week, start_time, end_time).
    """
    parser_classes = [MultiPartParser, FormParser]
    photo = request.FILES.get("photo") or request.FILES.get("image")
    if not photo:
        return Response(
            {"detail": "No image file provided. Use form field 'photo' or 'image'."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        image_bytes = photo.read()
    except Exception as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    if not image_bytes:
        return Response({"detail": "Empty image file."}, status=status.HTTP_400_BAD_REQUEST)

    content_type = getattr(photo, "content_type", None) or "image/jpeg"
    result = parse_schedule_image(image_bytes, content_type=content_type)
    if result.get("error"):
        return Response(
            {"detail": result["error"], "shifts": result.get("shifts", [])},
            status=status.HTTP_503_SERVICE_UNAVAILABLE if "OPENAI" in result["error"] else status.HTTP_400_BAD_REQUEST,
        )
    return Response(result)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsManagerOrAdmin])
def parse_schedule_document(request):
    """
    POST multipart: `file` (Excel .xlsx or CSV).
    Parses the document and adapts to its form/style (column names, date/time formats).
    Returns: template_name, shifts (employee_name, role, department, day_of_week, start_time, end_time).
    """
    doc = request.FILES.get("file") or request.FILES.get("document") or request.FILES.get("csv") or request.FILES.get("excel")
    if not doc:
        return Response(
            {"detail": "No file provided. Use form field 'file', 'document', 'csv', or 'excel'."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    name = getattr(doc, "name", "") or ""
    if not (name.lower().endswith(".csv") or name.lower().endswith(".xlsx") or name.lower().endswith(".xls")):
        return Response(
            {"detail": "Only Excel (.xlsx, .xls) or CSV files are supported."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        file_bytes = doc.read()
    except Exception as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    if not file_bytes:
        return Response({"detail": "Empty file."}, status=status.HTTP_400_BAD_REQUEST)

    content_type = getattr(doc, "content_type", None) or ""
    result = parse_schedule_document_service(file_bytes, filename=name, content_type=content_type)
    if result.get("error"):
        return Response(
            {"detail": result["error"], "shifts": result.get("shifts", []), "template_name": result.get("template_name")},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return Response(result)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsManagerOrAdmin])
def apply_parsed_schedule(request):
    """
    POST JSON:
    - template_name (optional)
    - shifts: [{ employee_name?, role, department?, day_of_week, start_time, end_time }]
    - save_as_template: bool — create ScheduleTemplate + TemplateShifts (role/day/time, no names)
    - week_start: "YYYY-MM-DD" (optional) — create WeeklySchedule and AssignedShifts for that week, matching names to staff
    """
    data = request.data
    if not isinstance(data, dict):
        return Response({"detail": "JSON body required."}, status=status.HTTP_400_BAD_REQUEST)

    shifts = data.get("shifts") or []
    template_name = (data.get("template_name") or "").strip() or "Imported from photo"
    save_as_template = data.get("save_as_template", False)
    week_start_str = data.get("week_start")
    user = request.user
    restaurant = getattr(user, "restaurant", None)
    if not restaurant:
        return Response({"detail": "User has no restaurant."}, status=status.HTTP_403_FORBIDDEN)

    # Normalize role for TemplateShift/AssignedShift
    from django.conf import settings
    valid_roles = {c[0] for c in getattr(settings, "STAFF_ROLES_CHOICES", [])}

    def norm_role(r):
        r = (r or "").strip().upper().replace(" ", "_")
        return r if r in valid_roles else "WAITER"

    created_template = None
    created_shift_ids = []

    if save_as_template:
        # Aggregate by (role, day_of_week, start_time, end_time) and create TemplateShift entries
        template = ScheduleTemplate.objects.create(
            restaurant=restaurant,
            name=template_name[:100],
            description="Created from schedule photo import",
            is_active=True,
        )
        created_template = {"id": str(template.id), "name": template.name}
        seen = set()
        for s in shifts:
            role = norm_role(s.get("role"))
            day = s.get("day_of_week")
            if day is None:
                continue
            start_str = (s.get("start_time") or "09:00")[:5]
            end_str = (s.get("end_time") or "17:00")[:5]
            try:
                start_t = datetime.strptime(start_str, "%H:%M").time()
                end_t = datetime.strptime(end_str, "%H:%M").time()
            except (ValueError, TypeError):
                start_t = _time(9, 0)
                end_t = _time(17, 0)
            key = (role, day, start_str, end_str)
            if key in seen:
                continue
            seen.add(key)
            TemplateShift.objects.create(
                template=template,
                role=role,
                day_of_week=int(day),
                start_time=start_t,
                end_time=end_t,
                required_staff=1,
            )

    if week_start_str:
        try:
            week_start = datetime.strptime(week_start_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return Response(
                {"detail": "week_start must be YYYY-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        week_end = week_start + timedelta(days=6)
        ws, _ = WeeklySchedule.objects.get_or_create(
            restaurant=restaurant,
            week_start=week_start,
            defaults={"week_end": week_end},
        )
        for s in shifts:
            day = s.get("day_of_week")
            if day is None:
                continue
            shift_date = week_start + timedelta(days=int(day))
            start_time_str = (s.get("start_time") or "09:00")[:5]
            end_time_str = (s.get("end_time") or "17:00")[:5]
            try:
                start_dt = timezone.make_aware(
                    timezone.datetime.combine(shift_date, datetime.strptime(start_time_str, "%H:%M").time())
                )
                end_dt = timezone.make_aware(
                    timezone.datetime.combine(shift_date, datetime.strptime(end_time_str, "%H:%M").time())
                )
            except (ValueError, TypeError):
                continue
            staff = None
            emp = (s.get("employee_name") or "").strip()
            if emp:
                staff = _match_employee_name_to_staff(emp, restaurant)
            assigned = AssignedShift.objects.create(
                schedule=ws,
                staff=staff,
                shift_date=shift_date,
                start_time=start_dt,
                end_time=end_dt,
                role=norm_role(s.get("role")),
                department=(s.get("department") or "")[:100] or None,
                status="SCHEDULED",
                created_by=user,
            )
            created_shift_ids.append(str(assigned.id))

    return Response(
        status=status.HTTP_201_CREATED,
        data={
            "template": created_template,
            "applied_shift_ids": created_shift_ids,
            "message": "Schedule applied successfully.",
        },
    )
