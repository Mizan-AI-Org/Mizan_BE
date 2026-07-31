"""JWT CRUD for personal WhatsApp reminders (dashboard UI)."""

from __future__ import annotations

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import permissions, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from scheduling.memory_models import PersonalReminder
from scheduling.views_memory import _reminder_payload


class PersonalRemindersUIView(APIView):
    """
    GET  /api/dashboard/personal-reminders/
    POST /api/dashboard/personal-reminders/  (create)
    PATCH /api/dashboard/personal-reminders/<uuid>/  (update / cancel)
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def _restaurant(self, request):
        return getattr(request.user, "restaurant", None)

    def get(self, request):
        restaurant = self._restaurant(request)
        if not restaurant:
            return Response({"error": "No workspace linked"}, status=400)
        status_filter = (request.query_params.get("status") or "pending").lower()
        qs = PersonalReminder.objects.filter(restaurant=restaurant, owner=request.user)
        if status_filter != "all":
            qs = qs.filter(status=status_filter)
        items = [_reminder_payload(r) for r in qs.order_by("due_at")[:50]]
        return Response({"success": True, "reminders": items, "count": len(items)})

    def post(self, request):
        restaurant = self._restaurant(request)
        if not restaurant:
            return Response({"error": "No workspace linked"}, status=400)
        data = request.data if isinstance(getattr(request, "data", None), dict) else {}
        title = str(data.get("title") or data.get("text") or "").strip()
        if not title:
            return Response({"error": "title required"}, status=400)
        due_raw = data.get("due_at") or data.get("when")
        due_at = parse_datetime(str(due_raw).replace("Z", "+00:00")) if due_raw else None
        if due_at is None:
            return Response({"error": "due_at required"}, status=400)
        if timezone.is_naive(due_at):
            due_at = timezone.make_aware(due_at, timezone.get_current_timezone())

        rem = PersonalReminder.objects.create(
            restaurant=restaurant,
            owner=request.user,
            phone=str(getattr(request.user, "phone", "") or "")[:40],
            title=title[:255],
            body=str(data.get("body") or data.get("description") or "")[:4000],
            due_at=due_at,
            timezone_name=str(data.get("timezone_name") or "Africa/Casablanca")[:64],
            recurrence=str(data.get("recurrence") or "none")[:20],
        )
        uploaded = request.FILES.get("attachment") or request.FILES.get("file")
        att_url = str(data.get("attachment_url") or "").strip()[:1024]
        if uploaded:
            rem.attachment.save(uploaded.name, uploaded, save=False)
        elif att_url:
            rem.attachment_url = att_url
        rem.save()
        return Response({"success": True, "reminder": _reminder_payload(rem)}, status=201)


class PersonalReminderDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def patch(self, request, pk):
        restaurant = getattr(request.user, "restaurant", None)
        if not restaurant:
            return Response({"error": "No workspace linked"}, status=400)
        rem = PersonalReminder.objects.filter(
            id=pk, restaurant=restaurant, owner=request.user
        ).first()
        if not rem:
            return Response({"error": "not found"}, status=404)

        data = request.data if isinstance(getattr(request, "data", None), dict) else {}
        action = str(data.get("action") or "").lower()
        if action == "cancel":
            rem.status = "cancelled"
            rem.save(update_fields=["status", "updated_at"])
            return Response({"success": True, "reminder": _reminder_payload(rem)})

        if data.get("title") or data.get("text"):
            rem.title = str(data.get("title") or data.get("text") or rem.title)[:255]
        if "body" in data or "description" in data:
            rem.body = str(data.get("body") or data.get("description") or "")[:4000]
        due_raw = data.get("due_at") or data.get("when")
        if due_raw:
            due_at = parse_datetime(str(due_raw).replace("Z", "+00:00"))
            if due_at:
                if timezone.is_naive(due_at):
                    due_at = timezone.make_aware(due_at, timezone.get_current_timezone())
                rem.due_at = due_at
        uploaded = request.FILES.get("attachment") or request.FILES.get("file")
        if uploaded:
            rem.attachment.save(uploaded.name, uploaded, save=False)
            rem.attachment_url = ""
        elif data.get("attachment_url"):
            rem.attachment_url = str(data.get("attachment_url"))[:1024]
        rem.save()
        return Response({"success": True, "reminder": _reminder_payload(rem)})
