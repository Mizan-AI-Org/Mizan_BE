"""
Agent API for WhatsApp-first memory layer (notes, lists, reminders, briefing, serendipity).
Auth: same as scheduling.views_agent — LUA_WEBHOOK_API_KEY or user JWT.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import permissions, status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from accounts.models import CustomUser
from .memory_models import MemoryList, MemoryListItem, MemoryNote, PersonalReminder
from .views_agent import _resolve_restaurant_for_agent, _try_jwt_restaurant_and_user

logger = logging.getLogger(__name__)


def _slugify_project(raw: str) -> str:
    s = (raw or "").strip().lower()
    s = re.sub(r"[^\w\s\-]", "", s, flags=re.UNICODE)
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s[:120]


def _as_list(val) -> list:
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str):
        parts = re.split(r"[,;|/]+", val)
        return [p.strip() for p in parts if p.strip()]
    return [str(val).strip()] if str(val).strip() else []


def _resolve_owner(request, restaurant, data: dict):
    """Prefer JWT user, then user_id / sender_phone from body.

    Managers, owners, and admins messaging from WhatsApp are first-class
    personal Memorae owners — same path as staff.
    """
    _, jwt_user = _try_jwt_restaurant_and_user(request)
    if jwt_user:
        return jwt_user

    uid = data.get("user_id") or data.get("owner_id")
    if uid:
        try:
            u = CustomUser.objects.filter(id=uid).select_related("restaurant").first()
            if u and getattr(u, "restaurant_id", None) == restaurant.id:
                return u
            # Multi-site managers: role on this restaurant
            if u and u.restaurant_roles.filter(restaurant=restaurant).exists():
                return u
        except Exception:
            pass

    phone = (
        data.get("sender_phone")
        or data.get("phone")
        or data.get("phoneNumber")
        or request.query_params.get("phone")
        or ""
    )
    phone = re.sub(r"\D", "", str(phone))
    if phone and len(phone) >= 8:
        from accounts.services import resolve_restaurant_and_staff_by_phone

        # Includes MANAGER / OWNER / ADMIN — not staff-only
        _, staff = resolve_restaurant_and_staff_by_phone(phone, exclude_super_admin=True)
        if staff and getattr(staff, "restaurant_id", None) == restaurant.id:
            return staff
        # Fallback: match phone digits on restaurant users (any role)
        for u in CustomUser.objects.filter(restaurant=restaurant).exclude(phone__isnull=True).exclude(phone=""):
            if re.sub(r"\D", "", str(u.phone or "")).endswith(phone[-9:]):
                return u
    return None


def _note_payload(n: MemoryNote) -> dict:
    return {
        "id": str(n.id),
        "content": n.content,
        "why": n.why or "",
        "people": n.people or [],
        "entities": n.entities or [],
        "project_key": n.project_key or "",
        "tags": n.tags or [],
        "visibility": n.visibility,
        "department": n.department or "",
        "media_url": n.media_url or "",
        "media_type": n.media_type or "",
        "linked_task_id": str(n.linked_task_id) if n.linked_task_id else None,
        "linked_staff_request_id": str(n.linked_staff_request_id) if n.linked_staff_request_id else None,
        "linked_invoice_id": str(n.linked_invoice_id) if n.linked_invoice_id else None,
        "owner_id": str(n.owner_id) if n.owner_id else None,
        "recall_count": n.recall_count,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


def _list_payload(lst: MemoryList, include_items: bool = True) -> dict:
    data = {
        "id": str(lst.id),
        "name": lst.name,
        "visibility": lst.visibility,
        "is_archived": lst.is_archived,
        "owner_id": str(lst.owner_id) if lst.owner_id else None,
    }
    if include_items:
        items = list(lst.items.order_by("order_index", "created_at"))
        data["items"] = [
            {
                "id": str(i.id),
                "text": i.text,
                "is_checked": i.is_checked,
                "order_index": i.order_index,
            }
            for i in items
        ]
        data["open_count"] = sum(1 for i in items if not i.is_checked)
        data["total_count"] = len(items)
    return data


def _reminder_payload(r: PersonalReminder) -> dict:
    return {
        "id": str(r.id),
        "title": r.title,
        "body": r.body or "",
        "due_at": r.due_at.isoformat() if r.due_at else None,
        "timezone_name": r.timezone_name,
        "recurrence": r.recurrence,
        "status": r.status,
        "phone": r.phone or "",
        "fire_count": r.fire_count,
        "linked_note_id": str(r.linked_note_id) if r.linked_note_id else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


# ─── Notes ───────────────────────────────────────────────────────────────────

@api_view(["GET", "POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_memory_notes(request):
    """
    GET: search/list notes. Query: q, project_key, visibility, limit
    POST: save a note (Memorae capture).
    """
    try:
        restaurant, acting_user, err = _resolve_restaurant_for_agent(request)
        if err:
            return Response({"error": err["error"]}, status=err["status"])

        data = request.data if isinstance(getattr(request, "data", None), dict) else {}
        owner = acting_user or _resolve_owner(request, restaurant, data)

        if request.method == "GET":
            q = (request.query_params.get("q") or data.get("q") or "").strip()
            project_key = _slugify_project(
                request.query_params.get("project_key") or data.get("project_key") or ""
            )
            visibility = (request.query_params.get("visibility") or "").strip()
            limit = min(int(request.query_params.get("limit") or 20), 50)

            qs = MemoryNote.objects.filter(restaurant=restaurant, is_archived=False)
            # Personal notes: owner sees own + team/department; others only team/department
            if owner:
                qs = qs.filter(
                    Q(visibility="team")
                    | Q(visibility="department")
                    | Q(visibility="personal", owner=owner)
                )
            else:
                qs = qs.filter(visibility__in=["team", "department"])

            if project_key:
                qs = qs.filter(project_key=project_key)
            if visibility in ("personal", "team", "department"):
                qs = qs.filter(visibility=visibility)
            if q:
                qs = qs.filter(
                    Q(search_text__icontains=q)
                    | Q(content__icontains=q)
                    | Q(why__icontains=q)
                    | Q(project_key__icontains=q)
                )

            notes = list(qs.order_by("-created_at")[:limit])
            # Mark recall
            if notes and q:
                now = timezone.now()
                MemoryNote.objects.filter(id__in=[n.id for n in notes]).update(
                    last_recalled_at=now,
                )
                for n in notes:
                    n.recall_count = (n.recall_count or 0) + 1
                    n.save(update_fields=["recall_count"])

            return Response(
                {
                    "success": True,
                    "notes": [_note_payload(n) for n in notes],
                    "count": len(notes),
                    "restaurant_id": str(restaurant.id),
                }
            )

        # POST — save
        content = (data.get("content") or data.get("text") or "").strip()
        if not content:
            return Response({"error": "content is required"}, status=status.HTTP_400_BAD_REQUEST)

        visibility = (data.get("visibility") or "personal").lower()
        if visibility not in ("personal", "team", "department"):
            visibility = "personal"

        project_raw = data.get("project_key") or data.get("project") or ""
        note = MemoryNote.objects.create(
            restaurant=restaurant,
            owner=owner,
            visibility=visibility,
            department=(data.get("department") or "")[:64],
            content=content,
            why=(data.get("why") or "")[:4000],
            people=_as_list(data.get("people")),
            entities=_as_list(data.get("entities")),
            project_key=_slugify_project(project_raw),
            tags=_as_list(data.get("tags")),
            linked_task_id=data.get("linked_task_id") or None,
            linked_staff_request_id=data.get("linked_staff_request_id") or None,
            linked_invoice_id=data.get("linked_invoice_id") or None,
            media_url=(data.get("media_url") or "")[:1000],
            media_type=(data.get("media_type") or "")[:40],
            source_channel=(data.get("source_channel") or "whatsapp")[:20],
            source_phone=re.sub(r"\D", "", str(data.get("sender_phone") or data.get("phone") or ""))[:40],
        )
        return Response(
            {
                "success": True,
                "note": _note_payload(note),
                "message": f"Saved. I'll remember this{' under ' + note.project_key if note.project_key else ''}.",
            },
            status=status.HTTP_201_CREATED,
        )
    except Exception as e:
        logger.exception("agent_memory_notes error")
        return Response({"error": str(e)[:200]}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST", "DELETE"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_memory_note_delete(request):
    try:
        restaurant, _, err = _resolve_restaurant_for_agent(request)
        if err:
            return Response({"error": err["error"]}, status=err["status"])
        data = request.data if isinstance(getattr(request, "data", None), dict) else {}
        note_id = data.get("note_id") or data.get("id")
        if not note_id:
            return Response({"error": "note_id required"}, status=status.HTTP_400_BAD_REQUEST)
        updated = MemoryNote.objects.filter(id=note_id, restaurant=restaurant).update(is_archived=True)
        return Response({"success": True, "archived": updated})
    except Exception as e:
        logger.exception("agent_memory_note_delete error")
        return Response({"error": str(e)[:200]}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ─── Lists ───────────────────────────────────────────────────────────────────

@api_view(["GET", "POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_memory_lists(request):
    """
    GET: list lists (optional name filter). POST actions via body.action:
      create | add_item | check_item | uncheck_item | show | archive
    """
    try:
        restaurant, acting_user, err = _resolve_restaurant_for_agent(request)
        if err:
            return Response({"error": err["error"]}, status=err["status"])

        data = request.data if isinstance(getattr(request, "data", None), dict) else {}
        owner = acting_user or _resolve_owner(request, restaurant, data)

        if request.method == "GET":
            name = (request.query_params.get("name") or "").strip()
            qs = MemoryList.objects.filter(restaurant=restaurant, is_archived=False)
            if owner:
                qs = qs.filter(Q(owner=owner) | Q(visibility__in=["team", "department"]))
            if name:
                qs = qs.filter(name__icontains=name)
            lists = [_list_payload(lst) for lst in qs.order_by("name")[:30]]
            return Response({"success": True, "lists": lists, "count": len(lists)})

        action = (data.get("action") or "create").lower()
        list_name = (data.get("name") or data.get("list_name") or "").strip()
        list_id = data.get("list_id")

        def _get_list():
            if list_id:
                return MemoryList.objects.filter(id=list_id, restaurant=restaurant).first()
            if list_name and owner:
                return MemoryList.objects.filter(
                    restaurant=restaurant, owner=owner, name__iexact=list_name
                ).first()
            if list_name:
                return MemoryList.objects.filter(
                    restaurant=restaurant, name__iexact=list_name
                ).first()
            return None

        if action == "create":
            if not list_name:
                return Response({"error": "name is required"}, status=status.HTTP_400_BAD_REQUEST)
            visibility = (data.get("visibility") or "personal").lower()
            if visibility not in ("personal", "team", "department"):
                visibility = "personal"
            lst, created = MemoryList.objects.get_or_create(
                restaurant=restaurant,
                owner=owner,
                name=list_name[:120],
                defaults={"visibility": visibility},
            )
            # Optional seed items
            items = _as_list(data.get("items"))
            for idx, text in enumerate(items):
                MemoryListItem.objects.create(
                    memory_list=lst, text=text[:500], order_index=idx
                )
            return Response(
                {
                    "success": True,
                    "created": created,
                    "list": _list_payload(lst),
                    "message": f"{'Created' if created else 'Opened'} list «{lst.name}».",
                },
                status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            )

        lst = _get_list()
        if not lst and action in ("add_item", "show") and list_name:
            # Auto-create on add
            lst = MemoryList.objects.create(
                restaurant=restaurant,
                owner=owner,
                name=list_name[:120],
                visibility=(data.get("visibility") or "personal"),
            )

        if not lst:
            return Response({"error": "list not found — provide name or list_id"}, status=status.HTTP_404_NOT_FOUND)

        if action == "show":
            return Response({"success": True, "list": _list_payload(lst)})

        if action == "add_item":
            text = (data.get("text") or data.get("item") or "").strip()
            if not text:
                return Response({"error": "text is required"}, status=status.HTTP_400_BAD_REQUEST)
            max_idx = lst.items.count()
            item = MemoryListItem.objects.create(
                memory_list=lst, text=text[:500], order_index=max_idx
            )
            return Response(
                {
                    "success": True,
                    "item": {"id": str(item.id), "text": item.text, "is_checked": False},
                    "list": _list_payload(lst),
                    "message": f"Added to «{lst.name}»: {item.text}",
                }
            )

        if action in ("check_item", "uncheck_item"):
            item_id = data.get("item_id")
            text = (data.get("text") or data.get("item") or "").strip()
            item = None
            if item_id:
                item = lst.items.filter(id=item_id).first()
            elif text:
                item = lst.items.filter(text__icontains=text).first()
            if not item:
                return Response({"error": "item not found"}, status=status.HTTP_404_NOT_FOUND)
            item.is_checked = action == "check_item"
            item.checked_at = timezone.now() if item.is_checked else None
            item.save(update_fields=["is_checked", "checked_at"])
            return Response(
                {
                    "success": True,
                    "list": _list_payload(lst),
                    "message": f"{'Checked' if item.is_checked else 'Unchecked'}: {item.text}",
                }
            )

        if action == "archive":
            lst.is_archived = True
            lst.save(update_fields=["is_archived", "updated_at"])
            return Response({"success": True, "message": f"Archived list «{lst.name}»."})

        return Response({"error": f"Unknown action: {action}"}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        logger.exception("agent_memory_lists error")
        return Response({"error": str(e)[:200]}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ─── Reminders ───────────────────────────────────────────────────────────────

@api_view(["GET", "POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_personal_reminders(request):
    """
    GET: pending reminders for owner.
    POST: create reminder, or action=cancel.
    """
    try:
        restaurant, acting_user, err = _resolve_restaurant_for_agent(request)
        if err:
            return Response({"error": err["error"]}, status=err["status"])

        data = request.data if isinstance(getattr(request, "data", None), dict) else {}
        owner = acting_user or _resolve_owner(request, restaurant, data)
        if not owner:
            return Response(
                {
                    "error": "owner_required",
                    "message": "Could not resolve who this reminder is for. Send from a registered WhatsApp number.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if request.method == "GET":
            status_filter = request.query_params.get("status") or "pending"
            qs = PersonalReminder.objects.filter(restaurant=restaurant, owner=owner)
            if status_filter != "all":
                qs = qs.filter(status=status_filter)
            items = [_reminder_payload(r) for r in qs.order_by("due_at")[:50]]
            return Response({"success": True, "reminders": items, "count": len(items)})

        action = (data.get("action") or "create").lower()
        if action == "cancel":
            rid = data.get("reminder_id") or data.get("id")
            if not rid:
                return Response({"error": "reminder_id required"}, status=status.HTTP_400_BAD_REQUEST)
            updated = PersonalReminder.objects.filter(
                id=rid, restaurant=restaurant, owner=owner
            ).update(status="cancelled")
            return Response({"success": True, "cancelled": updated})

        title = (data.get("title") or data.get("text") or "").strip()
        if not title:
            return Response({"error": "title is required"}, status=status.HTTP_400_BAD_REQUEST)

        due_raw = data.get("due_at") or data.get("when") or data.get("remind_at")
        if not due_raw:
            return Response(
                {"error": "due_at required (ISO datetime)"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        due_at = parse_datetime(str(due_raw).replace("Z", "+00:00"))
        if due_at is None:
            return Response({"error": "Invalid due_at"}, status=status.HTTP_400_BAD_REQUEST)
        if timezone.is_naive(due_at):
            due_at = timezone.make_aware(due_at)

        recurrence = (data.get("recurrence") or "none").lower()
        if recurrence not in ("none", "daily", "weekly", "monthly", "weekdays"):
            recurrence = "none"

        phone = re.sub(
            r"\D",
            "",
            str(data.get("sender_phone") or data.get("phone") or getattr(owner, "phone", "") or ""),
        )
        linked_note_id = data.get("linked_note_id")
        linked_note = None
        if linked_note_id:
            linked_note = MemoryNote.objects.filter(id=linked_note_id, restaurant=restaurant).first()

        rem = PersonalReminder.objects.create(
            restaurant=restaurant,
            owner=owner,
            phone=phone[:40],
            title=title[:255],
            body=(data.get("body") or data.get("description") or "")[:4000],
            due_at=due_at,
            timezone_name=(data.get("timezone") or "Africa/Casablanca")[:64],
            recurrence=recurrence,
            linked_note=linked_note,
        )
        return Response(
            {
                "success": True,
                "reminder": _reminder_payload(rem),
                "message": f"Got it — I'll remind you on WhatsApp at {due_at.isoformat()}.",
            },
            status=status.HTTP_201_CREATED,
        )
    except Exception as e:
        logger.exception("agent_personal_reminders error")
        return Response({"error": str(e)[:200]}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ─── Daily briefing + serendipity ────────────────────────────────────────────

@api_view(["GET", "POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_daily_briefing(request):
    """
    Build a personal WhatsApp briefing: due reminders, open list items, recent notes,
    and open assigned dashboard tasks.
    """
    try:
        restaurant, acting_user, err = _resolve_restaurant_for_agent(request)
        if err:
            return Response({"error": err["error"]}, status=err["status"])

        data = request.data if isinstance(getattr(request, "data", None), dict) else {}
        owner = acting_user or _resolve_owner(request, restaurant, {**data, **dict(request.query_params)})
        if not owner:
            return Response({"error": "owner_required"}, status=status.HTTP_400_BAD_REQUEST)

        now = timezone.now()
        end = now + timedelta(hours=36)

        reminders = list(
            PersonalReminder.objects.filter(
                restaurant=restaurant,
                owner=owner,
                status="pending",
                due_at__lte=end,
            ).order_by("due_at")[:10]
        )

        lists = MemoryList.objects.filter(
            restaurant=restaurant, owner=owner, is_archived=False
        ).prefetch_related("items")[:8]
        list_summaries = []
        for lst in lists:
            open_items = [i.text for i in lst.items.all() if not i.is_checked][:5]
            if open_items:
                list_summaries.append({"name": lst.name, "open_items": open_items})

        recent_notes = list(
            MemoryNote.objects.filter(
                restaurant=restaurant,
                is_archived=False,
            )
            .filter(Q(owner=owner) | Q(visibility__in=["team", "department"]))
            .order_by("-created_at")[:5]
        )

        open_tasks = []
        try:
            from dashboard.models import Task

            open_tasks = list(
                Task.objects.filter(
                    restaurant=restaurant,
                    assigned_to=owner,
                    status__in=["PENDING", "IN_PROGRESS"],
                ).order_by("due_date", "-priority")[:8]
            )
        except Exception:
            pass

        lines = [f"Good morning — here's your briefing for {restaurant.name}:"]
        if reminders:
            lines.append("\n⏰ Reminders:")
            for r in reminders:
                lines.append(f"  • {r.title} ({r.due_at.strftime('%a %H:%M')})")
        if open_tasks:
            lines.append("\n📋 Open tasks:")
            for t in open_tasks:
                lines.append(f"  • {t.title} [{t.status}]")
        if list_summaries:
            lines.append("\n📝 Lists:")
            for ls in list_summaries:
                lines.append(f"  • {ls['name']}: " + "; ".join(ls["open_items"]))
        if recent_notes:
            lines.append("\n💡 Recent memory:")
            for n in recent_notes:
                preview = (n.content or "")[:80]
                proj = f" [{n.project_key}]" if n.project_key else ""
                lines.append(f"  • {preview}{proj}")
        if len(lines) == 1:
            lines.append("\nNothing urgent on your plate. Have a great day!")

        text = "\n".join(lines)
        return Response(
            {
                "success": True,
                "briefing_text": text,
                "reminders": [_reminder_payload(r) for r in reminders],
                "open_tasks": [
                    {"id": str(t.id), "title": t.title, "status": t.status, "priority": t.priority}
                    for t in open_tasks
                ],
                "lists": list_summaries,
                "recent_notes": [_note_payload(n) for n in recent_notes],
                "owner_id": str(owner.id),
                "phone": re.sub(r"\D", "", str(getattr(owner, "phone", "") or "")),
            }
        )
    except Exception as e:
        logger.exception("agent_daily_briefing error")
        return Response({"error": str(e)[:200]}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET", "POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_memory_serendipity(request):
    """Resurface a random older note the user hasn't recalled recently (Memorae Park)."""
    try:
        restaurant, acting_user, err = _resolve_restaurant_for_agent(request)
        if err:
            return Response({"error": err["error"]}, status=err["status"])

        data = request.data if isinstance(getattr(request, "data", None), dict) else {}
        owner = acting_user or _resolve_owner(request, restaurant, {**data, **dict(request.query_params)})

        qs = MemoryNote.objects.filter(restaurant=restaurant, is_archived=False)
        if owner:
            qs = qs.filter(Q(owner=owner) | Q(visibility__in=["team", "department"]))
        else:
            qs = qs.filter(visibility__in=["team", "department"])

        # Prefer notes older than 7 days, least recently recalled
        cutoff = timezone.now() - timedelta(days=7)
        qs = qs.filter(created_at__lte=cutoff).order_by("last_recalled_at", "recall_count", "?")
        note = qs.first()
        if not note:
            note = (
                MemoryNote.objects.filter(restaurant=restaurant, is_archived=False)
                .order_by("?")
                .first()
            )
        if not note:
            return Response(
                {
                    "success": True,
                    "note": None,
                    "message": "No memories to resurface yet — save something with «save this…».",
                }
            )

        note.last_recalled_at = timezone.now()
        note.recall_count = (note.recall_count or 0) + 1
        note.save(update_fields=["last_recalled_at", "recall_count"])

        return Response(
            {
                "success": True,
                "note": _note_payload(note),
                "message": f"Remember this? {note.content[:200]}",
            }
        )
    except Exception as e:
        logger.exception("agent_memory_serendipity error")
        return Response({"error": str(e)[:200]}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_reminders_due(request):
    """
    Internal sweep endpoint: list pending reminders due now (for Celery / Lua jobs).
    Query: within_minutes (default 5), limit (default 100).
    Optional restaurant_id to scope; omit for all-tenant sweep (agent key required).
    """
    try:
        from .views_agent import validate_agent_key

        is_valid, error = validate_agent_key(request)
        if not is_valid:
            return Response({"error": error}, status=status.HTTP_401_UNAUTHORIZED)

        restaurant = None
        rid = (
            request.query_params.get("restaurant_id")
            or request.headers.get("X-Restaurant-Id")
            or ""
        ).strip()
        if rid:
            from accounts.models import Restaurant

            restaurant = Restaurant.objects.filter(id=rid).first()

        within = int(request.query_params.get("within_minutes") or 5)
        limit = min(int(request.query_params.get("limit") or 100), 500)
        now = timezone.now()
        qs = PersonalReminder.objects.filter(
            status="pending",
            due_at__lte=now + timedelta(minutes=within),
        ).select_related("owner", "restaurant", "linked_note")
        if restaurant:
            qs = qs.filter(restaurant=restaurant)
        due = list(qs.order_by("due_at")[:limit])
        return Response(
            {
                "success": True,
                "reminders": [
                    {
                        **_reminder_payload(r),
                        "restaurant_id": str(r.restaurant_id),
                        "owner_name": (
                            f"{getattr(r.owner, 'first_name', '')} {getattr(r.owner, 'last_name', '')}".strip()
                            or getattr(r.owner, "email", "")
                        ),
                        "linked_note": _note_payload(r.linked_note) if r.linked_note_id else None,
                    }
                    for r in due
                ],
                "count": len(due),
            }
        )
    except Exception as e:
        logger.exception("agent_reminders_due error")
        return Response({"error": str(e)[:200]}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_reminder_mark_fired(request):
    """Mark reminder fired and schedule next occurrence if recurring."""
    try:
        from .views_agent import validate_agent_key
        from dateutil.relativedelta import relativedelta

        is_valid, error = validate_agent_key(request)
        if not is_valid:
            return Response({"error": error}, status=status.HTTP_401_UNAUTHORIZED)

        data = request.data if isinstance(getattr(request, "data", None), dict) else {}
        rid = data.get("reminder_id") or data.get("id")
        if not rid:
            return Response({"error": "reminder_id required"}, status=status.HTTP_400_BAD_REQUEST)

        rem = PersonalReminder.objects.filter(id=rid).first()
        if not rem:
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)

        now = timezone.now()
        rem.fired_at = now
        rem.fire_count = (rem.fire_count or 0) + 1

        if rem.recurrence == "none":
            rem.status = "fired"
        else:
            # Roll forward
            if rem.recurrence == "daily":
                rem.due_at = rem.due_at + timedelta(days=1)
            elif rem.recurrence == "weekly":
                rem.due_at = rem.due_at + timedelta(weeks=1)
            elif rem.recurrence == "monthly":
                rem.due_at = rem.due_at + relativedelta(months=1)
            elif rem.recurrence == "weekdays":
                rem.due_at = rem.due_at + timedelta(days=1)
                while rem.due_at.weekday() >= 5:  # Sat/Sun
                    rem.due_at = rem.due_at + timedelta(days=1)
            rem.status = "pending"

        rem.save()
        return Response({"success": True, "reminder": _reminder_payload(rem)})
    except Exception as e:
        logger.exception("agent_reminder_mark_fired error")
        return Response({"error": str(e)[:200]}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
