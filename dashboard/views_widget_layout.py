"""
Persisted dashboard widget layout per user + agent API for Miya/Lua to add widgets
and create custom dashboard tiles (custom:<uuid>).
"""

import logging
import uuid

from django.conf import settings
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import CustomUser
from .models import DashboardCustomWidget
from .widget_ids import (
    ALLOWED_CUSTOM_WIDGET_ICONS,
    CUSTOM_WIDGET_PREFIX,
    DASHBOARD_WIDGET_IDS,
    DEFAULT_DASHBOARD_WIDGET_ORDER,
)

logger = logging.getLogger(__name__)


def _validate_agent_key(request):
    expected = getattr(settings, "LUA_WEBHOOK_API_KEY", None)
    if not expected:
        return False, "Agent key not configured"
    auth = request.headers.get("Authorization") or ""
    if auth != f"Bearer {expected}":
        return False, "Unauthorized"
    return True, None


def _parse_custom_slot_id(s: str) -> uuid.UUID | None:
    if not isinstance(s, str) or not s.startswith(CUSTOM_WIDGET_PREFIX):
        return None
    rest = s[len(CUSTOM_WIDGET_PREFIX) :].strip()
    try:
        return uuid.UUID(rest)
    except ValueError:
        return None


def _clean_order(raw, user: CustomUser | None) -> list[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        return None
    out: list[str] = []
    seen: set[str] = set()
    for x in raw:
        if not isinstance(x, str) or x in seen:
            continue
        if x in DASHBOARD_WIDGET_IDS:
            seen.add(x)
            out.append(x)
            continue
        cid = _parse_custom_slot_id(x)
        if cid and user is not None:
            if DashboardCustomWidget.objects.filter(id=cid, user=user).exists():
                seen.add(x)
                out.append(x)
    return out


def _can_customize_dashboard(user: CustomUser) -> bool:
    return user.role in ("SUPER_ADMIN", "ADMIN", "MANAGER", "OWNER")


def _resolve_user_from_agent_payload(data: dict) -> CustomUser | None:
    user = None
    uid = data.get("user_id") or data.get("userId")
    if uid:
        try:
            user = CustomUser.objects.filter(id=uid, is_active=True).first()
        except Exception:
            user = None
    if user is None and data.get("email"):
        user = CustomUser.objects.filter(email__iexact=str(data["email"]).strip(), is_active=True).first()
    if user is None and data.get("phone"):
        from staff.views_agent import _resolve_restaurant_and_staff_by_phone

        _rest, staff = _resolve_restaurant_and_staff_by_phone(data.get("phone"))
        user = staff
    return user


class DashboardWidgetOrderView(APIView):
    """GET/PATCH widget order for the authenticated user."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        cleaned = _clean_order(getattr(user, "dashboard_widget_order", None), user)
        return Response({"order": cleaned})

    def patch(self, request):
        user = request.user
        if not _can_customize_dashboard(user):
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        body = request.data or {}
        if "add" in body:
            to_add = body.get("add") or []
            if not isinstance(to_add, list):
                return Response({"detail": "add must be a list"}, status=status.HTTP_400_BAD_REQUEST)
            current = _clean_order(user.dashboard_widget_order, user)
            if current is None:
                current = list(DEFAULT_DASHBOARD_WIDGET_ORDER)
            for w in to_add:
                if isinstance(w, str) and w in DASHBOARD_WIDGET_IDS and w not in current:
                    current.append(w)
            user.dashboard_widget_order = current
            user.save(update_fields=["dashboard_widget_order"])
            return Response({"order": current})

        if "order" in body:
            order = body.get("order")
            cleaned = _clean_order(order, user)
            if cleaned is None:
                return Response({"detail": "order must be a list of valid widget ids"}, status=status.HTTP_400_BAD_REQUEST)
            user.dashboard_widget_order = cleaned
            user.save(update_fields=["dashboard_widget_order"])
            return Response({"order": cleaned})

        return Response({"detail": "Expected add or order"}, status=status.HTTP_400_BAD_REQUEST)


class DashboardCustomWidgetListView(APIView):
    """List Miya-created custom widgets for the current user (for rendering)."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = DashboardCustomWidget.objects.filter(user=request.user).order_by("-created_at")
        widgets = []
        for w in qs:
            widgets.append(
                {
                    "id": str(w.id),
                    "slot_id": w.slot_id(),
                    "title": w.title,
                    "subtitle": w.subtitle or "",
                    "link_url": w.link_url or "",
                    "icon": w.icon or "sparkles",
                    "created_at": w.created_at.isoformat() if w.created_at else None,
                }
            )
        return Response({"widgets": widgets})


class AgentDashboardWidgetsAddView(APIView):
    """
    Miya/Lua: add one or more built-in dashboard widgets for a manager user.
    Auth: Bearer LUA_WEBHOOK_API_KEY

    Body:
      - widgets: required list of widget id strings
      - user_id: optional UUID string
      - email: optional manager email
      - phone: optional phone (digits); resolves first active user with matching phone
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        ok, err = _validate_agent_key(request)
        if not ok:
            return Response({"success": False, "error": err}, status=status.HTTP_401_UNAUTHORIZED)

        data = request.data or {}
        widgets = data.get("widgets") or data.get("widget_ids") or []
        if not isinstance(widgets, list) or not widgets:
            return Response(
                {"success": False, "error": "widgets must be a non-empty list of widget ids"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        unknown = [w for w in widgets if not isinstance(w, str) or w not in DASHBOARD_WIDGET_IDS]
        if unknown:
            return Response(
                {"success": False, "error": f"Invalid widget ids: {unknown}", "allowed": sorted(DASHBOARD_WIDGET_IDS)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = _resolve_user_from_agent_payload(data)

        if user is None:
            return Response(
                {"success": False, "error": "Could not resolve user; pass user_id, email, or phone"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not _can_customize_dashboard(user):
            return Response(
                {"success": False, "error": "User cannot customize dashboard (need manager role)"},
                status=status.HTTP_403_FORBIDDEN,
            )

        current = _clean_order(user.dashboard_widget_order, user)
        if current is None:
            current = list(DEFAULT_DASHBOARD_WIDGET_ORDER)
        added = []
        for w in widgets:
            if w not in current:
                current.append(w)
                added.append(w)
        user.dashboard_widget_order = current
        user.save(update_fields=["dashboard_widget_order"])

        return Response(
            {
                "success": True,
                "user_id": str(user.id),
                "order": current,
                "added": added,
                "message_for_user": (
                    f"Added {len(added)} widget(s) to your dashboard: {', '.join(added)}. "
                    "Open or refresh the dashboard to see them."
                    if added
                    else "Those widgets are already on your dashboard."
                ),
            }
        )


class AgentDashboardWidgetCreateView(APIView):
    """
    Miya/Lua: create a new custom dashboard tile and optionally add it to the user's layout.

    Auth: Bearer LUA_WEBHOOK_API_KEY

    Body:
      - title: required string
      - subtitle: optional
      - link_url or link: optional (app path e.g. /dashboard/processes-tasks-app or https://...)
      - icon: optional; one of ALLOWED_CUSTOM_WIDGET_ICONS (default sparkles)
      - add_to_dashboard: optional bool (default true)
      - user_id | email | phone: target user
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        ok, err = _validate_agent_key(request)
        if not ok:
            return Response({"success": False, "error": err}, status=status.HTTP_401_UNAUTHORIZED)

        data = request.data or {}
        title = (data.get("title") or "").strip()
        if not title:
            return Response({"success": False, "error": "title is required"}, status=status.HTTP_400_BAD_REQUEST)

        subtitle = str(data.get("subtitle") or "")[:2000]
        link_url = str(data.get("link_url") or data.get("link") or "")[:2048].strip()

        icon_raw = (data.get("icon") or "sparkles").strip().lower()[:64]
        if icon_raw not in ALLOWED_CUSTOM_WIDGET_ICONS:
            icon_raw = "sparkles"

        add_to_dashboard = data.get("add_to_dashboard", True)
        if isinstance(add_to_dashboard, str):
            add_to_dashboard = add_to_dashboard.lower() in ("1", "true", "yes")

        user = _resolve_user_from_agent_payload(data)
        if user is None:
            return Response(
                {"success": False, "error": "Could not resolve user; pass user_id, email, or phone"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not _can_customize_dashboard(user):
            return Response(
                {"success": False, "error": "User cannot customize dashboard (need manager role)"},
                status=status.HTTP_403_FORBIDDEN,
            )

        if not getattr(user, "restaurant_id", None):
            return Response({"success": False, "error": "User has no restaurant"}, status=status.HTTP_400_BAD_REQUEST)

        w = DashboardCustomWidget.objects.create(
            user=user,
            restaurant_id=user.restaurant_id,
            title=title,
            subtitle=subtitle,
            link_url=link_url,
            icon=icon_raw,
        )
        slot = w.slot_id()

        if add_to_dashboard:
            current = _clean_order(user.dashboard_widget_order, user)
            if current is None:
                current = list(DEFAULT_DASHBOARD_WIDGET_ORDER)
            if slot not in current:
                current.append(slot)
            user.dashboard_widget_order = current
            user.save(update_fields=["dashboard_widget_order"])

        return Response(
            {
                "success": True,
                "widget_id": slot,
                "user_id": str(user.id),
                "order": _clean_order(user.dashboard_widget_order, user) if add_to_dashboard else None,
                "widget": {
                    "id": str(w.id),
                    "title": w.title,
                    "subtitle": w.subtitle,
                    "link_url": w.link_url,
                    "icon": w.icon,
                },
                "message_for_user": (
                    f'Added a new dashboard card "{w.title}". Open or refresh the dashboard to see it.'
                    if add_to_dashboard
                    else f'Created dashboard card "{w.title}" (slot {slot}).'
                ),
            },
            status=status.HTTP_201_CREATED,
        )
