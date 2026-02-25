"""
Agent-authenticated schedule import: parse photo/document (base64) and apply parsed schedule.
Miya can import schedules from WhatsApp when the manager sends a photo or file.
"""
import base64
import logging
from datetime import datetime, timedelta, time as _time

from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from accounts.models import CustomUser
from core.utils import resolve_agent_restaurant_and_user

from .models import ScheduleTemplate, TemplateShift, WeeklySchedule, AssignedShift
from .schedule_photo_service import parse_schedule_image
from .schedule_document_service import parse_schedule_document as parse_schedule_document_service
from .schedule_photo_views import _match_employee_name_to_staff

logger = logging.getLogger(__name__)


def _validate_agent_key(request):
    auth_header = request.headers.get("Authorization")
    expected = getattr(settings, "LUA_WEBHOOK_API_KEY", None)
    if not expected:
        return False, Response(
            {"detail": "Agent key not configured"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    if not auth_header or auth_header != f"Bearer {expected}":
        return False, Response(
            {"detail": "Unauthorized"},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    return True, None


def _resolve_restaurant_and_user(request):
    rid = request.META.get("HTTP_X_RESTAURANT_ID") or (request.data if hasattr(request, "data") else {}).get("restaurant_id") or (request.query_params.get("restaurant_id") if hasattr(request, "query_params") else None)
    if rid:
        from accounts.models import Restaurant
        try:
            restaurant = Restaurant.objects.get(id=rid)
            acting_user = CustomUser.objects.filter(
                restaurant=restaurant,
                role__in=["MANAGER", "ADMIN", "SUPER_ADMIN"],
                is_active=True,
            ).first()
            return restaurant, acting_user
        except Restaurant.DoesNotExist:
            pass
    restaurant, acting_user = resolve_agent_restaurant_and_user(
        request=request,
        payload=request.data if hasattr(request, "data") and isinstance(getattr(request, "data"), dict) else {},
    )
    if not restaurant:
        return None, None
    if not acting_user:
        acting_user = CustomUser.objects.filter(
            restaurant=restaurant,
            role__in=["MANAGER", "ADMIN", "SUPER_ADMIN"],
            is_active=True,
        ).first()
    return restaurant, acting_user


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def agent_parse_schedule_photo(request):
    """
    POST JSON: { "base64_image": "<base64 string>", "content_type": "image/jpeg" (optional) }.
    Returns: template_name, shifts (same as parse_schedule_photo).
    Auth: Bearer LUA_WEBHOOK_API_KEY.
    """
    ok, err_response = _validate_agent_key(request)
    if not ok:
        return err_response

    data = getattr(request, "data", None) or {}
    b64 = data.get("base64_image") or data.get("image") or data.get("image_base64")
    if not b64:
        return Response(
            {"detail": "base64_image is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        image_bytes = base64.b64decode(b64)
    except Exception as e:
        return Response(
            {"detail": f"Invalid base64: {e}"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not image_bytes:
        return Response({"detail": "Empty image."}, status=status.HTTP_400_BAD_REQUEST)

    content_type = (data.get("content_type") or "image/jpeg").strip() or "image/jpeg"
    result = parse_schedule_image(image_bytes, content_type=content_type)
    if result.get("error"):
        return Response(
            {"detail": result["error"], "shifts": result.get("shifts", [])},
            status=status.HTTP_503_SERVICE_UNAVAILABLE if "OPENAI" in (result.get("error") or "") else status.HTTP_400_BAD_REQUEST,
        )
    return Response(result)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def agent_parse_schedule_document(request):
    """
    POST JSON: { "base64_content": "<base64 string>", "filename": "schedule.xlsx" }.
    filename must end with .csv, .xlsx, or .xls.
    Returns: template_name, shifts.
    Auth: Bearer LUA_WEBHOOK_API_KEY.
    """
    ok, err_response = _validate_agent_key(request)
    if not ok:
        return err_response

    data = getattr(request, "data", None) or {}
    b64 = data.get("base64_content") or data.get("content") or data.get("file_base64")
    filename = (data.get("filename") or "schedule.csv").strip()
    if not b64:
        return Response(
            {"detail": "base64_content is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not (filename.lower().endswith(".csv") or filename.lower().endswith(".xlsx") or filename.lower().endswith(".xls")):
        return Response(
            {"detail": "Only Excel (.xlsx, .xls) or CSV files are supported."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        file_bytes = base64.b64decode(b64)
    except Exception as e:
        return Response(
            {"detail": f"Invalid base64: {e}"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not file_bytes:
        return Response({"detail": "Empty file."}, status=status.HTTP_400_BAD_REQUEST)

    content_type = "text/csv" if filename.lower().endswith(".csv") else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    result = parse_schedule_document_service(file_bytes, filename=filename, content_type=content_type)
    if result.get("error"):
        return Response(
            {"detail": result["error"], "shifts": result.get("shifts", []), "template_name": result.get("template_name")},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return Response(result)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def agent_apply_parsed_schedule(request):
    """
    POST JSON: restaurant_id (or X-Restaurant-Id), template_name?, shifts, save_as_template?, week_start?.
    Same semantics as apply_parsed_schedule; uses first manager as acting_user when no JWT.
    Auth: Bearer LUA_WEBHOOK_API_KEY.
    """
    ok, err_response = _validate_agent_key(request)
    if not ok:
        return err_response

    data = getattr(request, "data", None) or {}
    if not isinstance(data, dict):
        return Response({"detail": "JSON body required."}, status=status.HTTP_400_BAD_REQUEST)

    restaurant, acting_user = _resolve_restaurant_and_user(request)
    if not restaurant:
        return Response(
            {"detail": "Unable to resolve restaurant (provide restaurant_id or X-Restaurant-Id)."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    shifts = data.get("shifts") or []
    template_name = (data.get("template_name") or "").strip() or "Imported from photo"
    save_as_template = data.get("save_as_template", False)
    week_start_str = data.get("week_start")

    from django.conf import settings as django_settings
    valid_roles = {c[0] for c in getattr(django_settings, "STAFF_ROLES_CHOICES", [])}

    def norm_role(r):
        r = (r or "").strip().upper().replace(" ", "_")
        return r if r in valid_roles else "WAITER"

    created_template = None
    created_shift_ids = []

    if save_as_template:
        template = ScheduleTemplate.objects.create(
            restaurant=restaurant,
            name=template_name[:100],
            description="Created from schedule import (agent)",
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
                created_by=acting_user,
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
