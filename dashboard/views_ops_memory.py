"""
Phase 2 ops helpers: manager validation, photo proof, absent assignee flag,
department default owners, free-text check-in classification.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta

from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

logger = logging.getLogger(__name__)


def _resolve_dashboard_restaurant(request):
    from scheduling.views_agent import _resolve_restaurant_for_agent

    return _resolve_restaurant_for_agent(request)


def _is_user_absent(user, restaurant, on_date=None) -> bool:
    """True if user has approved time off covering today (no auto-reassign)."""
    if not user:
        return False
    from scheduling.models import TimeOffRequest

    day = on_date or timezone.localdate()
    return TimeOffRequest.objects.filter(
        staff=user,
        status="APPROVED",
        start_date__lte=day,
        end_date__gte=day,
    ).exists()


@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_validate_task(request):
    """Manager validates any task (cross-cutting). Non-blocking label clears."""
    try:
        restaurant, acting_user, err = _resolve_dashboard_restaurant(request)
        if err:
            return Response({"error": err["error"]}, status=err["status"])
        data = request.data if isinstance(getattr(request, "data", None), dict) else {}
        task_id = data.get("task_id") or data.get("id")
        if not task_id:
            return Response({"error": "task_id required"}, status=status.HTTP_400_BAD_REQUEST)

        from dashboard.models import Task

        task = Task.objects.filter(id=task_id, restaurant=restaurant).first()
        if not task:
            return Response({"error": "task not found"}, status=status.HTTP_404_NOT_FOUND)

        task.requires_manager_validation = True  # ensure flag was on
        task.manager_validated_at = timezone.now()
        task.manager_validated_by = acting_user
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
                "message": f"Validated: {task.title}",
            }
        )
    except Exception as e:
        logger.exception("agent_validate_task")
        return Response({"error": str(e)[:200]}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_submit_task_proof(request):
    """Staff submits photo proof of work via WhatsApp."""
    try:
        restaurant, acting_user, err = _resolve_dashboard_restaurant(request)
        if err:
            return Response({"error": err["error"]}, status=err["status"])
        data = request.data if isinstance(getattr(request, "data", None), dict) else {}
        task_id = data.get("task_id") or data.get("id")
        media_url = (data.get("media_url") or data.get("photo_url") or "").strip()
        if not task_id or not media_url:
            return Response(
                {"error": "task_id and media_url required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from dashboard.models import Task

        task = Task.objects.filter(id=task_id, restaurant=restaurant).first()
        if not task:
            return Response({"error": "task not found"}, status=status.HTTP_404_NOT_FOUND)

        task.proof_media_url = media_url[:1000]
        task.proof_submitted_at = timezone.now()
        # Optionally mark in progress
        if task.status == "PENDING":
            task.status = "IN_PROGRESS"
        task.save(
            update_fields=["proof_media_url", "proof_submitted_at", "status", "updated_at"]
        )
        return Response(
            {
                "success": True,
                "task_id": str(task.id),
                "proof_media_url": task.proof_media_url,
                "message": "Photo proof saved. Thanks!",
            }
        )
    except Exception as e:
        logger.exception("agent_submit_task_proof")
        return Response({"error": str(e)[:200]}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET", "POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_department_owners(request):
    """
    Get/set who is automatically assigned per department/category.
    Stored on Restaurant.general_settings['category_owners'].
    """
    try:
        restaurant, _, err = _resolve_dashboard_restaurant(request)
        if err:
            return Response({"error": err["error"]}, status=err["status"])

        settings_blob = restaurant.general_settings if isinstance(restaurant.general_settings, dict) else {}
        owners = settings_blob.get("category_owners") or {}

        if request.method == "GET":
            return Response({"success": True, "category_owners": owners})

        data = request.data if isinstance(getattr(request, "data", None), dict) else {}
        updates = data.get("category_owners") or data.get("owners") or {}
        if not isinstance(updates, dict):
            return Response({"error": "category_owners must be an object"}, status=status.HTTP_400_BAD_REQUEST)

        # Merge
        owners = {**owners, **{str(k).upper(): str(v) for k, v in updates.items() if v}}
        settings_blob["category_owners"] = owners
        restaurant.general_settings = settings_blob
        restaurant.save(update_fields=["general_settings", "updated_at"])
        return Response(
            {
                "success": True,
                "category_owners": owners,
                "message": "Department assignees updated.",
            }
        )
    except Exception as e:
        logger.exception("agent_department_owners")
        return Response({"error": str(e)[:200]}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_search_tasks_and_staff(request):
    """Cross-cutting search: any task or staff member + what's assigned to them."""
    try:
        restaurant, _, err = _resolve_dashboard_restaurant(request)
        if err:
            return Response({"error": err["error"]}, status=err["status"])

        q = (request.query_params.get("q") or "").strip()
        if not q or len(q) < 2:
            return Response({"error": "q must be at least 2 characters"}, status=status.HTTP_400_BAD_REQUEST)

        from accounts.models import CustomUser
        from dashboard.models import Task
        from staff.models import StaffRequest
        from django.db.models import Q

        staff_hits = list(
            CustomUser.objects.filter(restaurant=restaurant)
            .filter(
                Q(first_name__icontains=q)
                | Q(last_name__icontains=q)
                | Q(email__icontains=q)
                | Q(phone__icontains=q)
            )[:15]
        )

        tasks = list(
            Task.objects.filter(restaurant=restaurant)
            .filter(Q(title__icontains=q) | Q(description__icontains=q))
            .select_related("assigned_to")[:20]
        )
        requests_hits = list(
            StaffRequest.objects.filter(restaurant=restaurant)
            .filter(Q(subject__icontains=q) | Q(description__icontains=q))
            .select_related("assignee", "staff")[:20]
        )

        staff_payload = []
        for u in staff_hits:
            assigned = list(
                Task.objects.filter(
                    restaurant=restaurant,
                    assigned_to=u,
                    status__in=["PENDING", "IN_PROGRESS"],
                ).values("id", "title", "status", "priority")[:10]
            )
            absent = _is_user_absent(u, restaurant)
            staff_payload.append(
                {
                    "id": str(u.id),
                    "name": f"{u.first_name or ''} {u.last_name or ''}".strip() or u.email,
                    "phone": u.phone or "",
                    "role": getattr(u, "role", "") or "",
                    "is_absent": absent,
                    "open_tasks": [
                        {"id": str(t["id"]), "title": t["title"], "status": t["status"]}
                        for t in assigned
                    ],
                }
            )

        def _task_row(t: Task):
            absent = _is_user_absent(t.assigned_to, restaurant) if t.assigned_to_id else False
            validated = bool(t.manager_validated_at) if t.requires_manager_validation else None
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
                "manager_validated": validated,
                "validation_label": (
                    None
                    if not t.requires_manager_validation
                    else ("validated" if t.manager_validated_at else "not validated by manager")
                ),
                "has_photo_proof": bool(t.proof_media_url),
            }

        return Response(
            {
                "success": True,
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
                    }
                    for r in requests_hits
                ],
            }
        )
    except Exception as e:
        logger.exception("agent_search_tasks_and_staff")
        return Response({"error": str(e)[:200]}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_classify_checkin_message(request):
    """
    Free-text check-in companion: classify messages like "I'll be late" / "stuck in traffic".
    Logs against the employee (no department routing). Does not replace GPS clock-in.
    """
    try:
        restaurant, acting_user, err = _resolve_dashboard_restaurant(request)
        if err:
            return Response({"error": err["error"]}, status=err["status"])

        data = request.data if isinstance(getattr(request, "data", None), dict) else {}
        text = (data.get("text") or data.get("message") or "").strip()
        if not text:
            return Response({"error": "text required"}, status=status.HTTP_400_BAD_REQUEST)

        lower = text.lower()
        label = "note"
        if re.search(r"\b(late|retard|غادي نتاخر|ghadi ntakher|stuck|traffic|embouteillage)\b", lower):
            label = "running_late"
        elif re.search(r"\b(sick|malade|مريض|can't come|ne peux pas|absent)\b", lower):
            label = "absence"
        elif re.search(r"\b(here|arriv|je suis|wslt|وصلت|clock.?in|pointer)\b", lower):
            label = "arrival"
        elif re.search(r"\b(leave early|partir|غادي نمشي)\b", lower):
            label = "leaving_early"

        # Persist as personal memory note + optional dashboard task for managers
        from scheduling.memory_models import MemoryNote
        from scheduling.views_memory import _resolve_owner

        owner = acting_user or _resolve_owner(request, restaurant, data)
        note = MemoryNote.objects.create(
            restaurant=restaurant,
            owner=owner,
            visibility="team",
            content=text,
            why=f"check-in free-text: {label}",
            tags=["check-in", label],
            project_key="check-in",
            entities=[label],
            source_channel="whatsapp",
            source_phone=re.sub(r"\D", "", str(data.get("sender_phone") or data.get("phone") or ""))[:40],
        )

        # Surface late/absence to managers as a lightweight task
        task_id = None
        if label in ("running_late", "absence", "leaving_early") and owner:
            from dashboard.models import Task

            who = f"{owner.first_name or ''} {owner.last_name or ''}".strip() or "Staff"
            titles = {
                "running_late": f"{who} running late",
                "absence": f"{who} reported absence",
                "leaving_early": f"{who} leaving early",
            }
            t = Task.objects.create(
                restaurant=restaurant,
                assigned_to=None,
                title=titles[label],
                description=text,
                priority="HIGH" if label == "absence" else "MEDIUM",
                category="SCHEDULING",
                source="WHATSAPP",
                source_label="Check-in message",
                ai_summary=f"Classified as {label}",
                follow_up_enabled=False,
            )
            task_id = str(t.id)

        return Response(
            {
                "success": True,
                "classification": label,
                "note_id": str(note.id),
                "task_id": task_id,
                "message": f"Logged as {label.replace('_', ' ')} against your profile.",
            }
        )
    except Exception as e:
        logger.exception("agent_classify_checkin_message")
        return Response({"error": str(e)[:200]}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_detect_order_station(request):
    """
    Auto-detect Bar / Floor / Kitchen from sender role.
    Ask for clarification only if role is unclear.
    """
    try:
        restaurant, acting_user, err = _resolve_dashboard_restaurant(request)
        if err:
            return Response({"error": err["error"]}, status=err["status"])

        data = request.data if isinstance(getattr(request, "data", None), dict) else {}
        role = (
            data.get("role")
            or getattr(acting_user, "role", None)
            or getattr(acting_user, "position", None)
            or ""
        )
        role_l = str(role).lower()
        station = None
        if re.search(r"bar|bartender|barman|mixolog", role_l):
            station = "Bar"
        elif re.search(r"chef|kitchen|cook|cuisine|commis", role_l):
            station = "Kitchen"
        elif re.search(r"wait|server|floor|service|host|runner", role_l):
            station = "Floor"

        if not station:
            return Response(
                {
                    "success": True,
                    "station": None,
                    "needs_clarification": True,
                    "message": "Which station is this for — Bar, Floor, Kitchen, or Other?",
                }
            )
        return Response(
            {
                "success": True,
                "station": station,
                "needs_clarification": False,
                "message": f"Detected station: {station}",
            }
        )
    except Exception as e:
        logger.exception("agent_detect_order_station")
        return Response({"error": str(e)[:200]}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
