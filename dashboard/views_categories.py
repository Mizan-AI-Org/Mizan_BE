"""
User-facing REST endpoints for manager-created dashboard widget categories
and per-user management of custom dashboard widgets (rename/delete/move/create).

Categories are tenant-wide; only SUPER_ADMIN/ADMIN/OWNER/MANAGER can mutate them.
Custom widgets are user-scoped; the owner or a manager in the same tenant may
rename / delete / recategorize them.
"""

from __future__ import annotations

import logging
import uuid

from django.db import IntegrityError
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import DashboardCategory, DashboardCustomWidget
from .views_widget_layout import (
    _can_customize_dashboard,
    _clean_order,
)
from .widget_ids import (
    ALLOWED_CUSTOM_WIDGET_ICONS,
    DEFAULT_DASHBOARD_WIDGET_ORDER,
)
from .widget_link_resolver import ensure_link

logger = logging.getLogger(__name__)


_MAX_CATEGORY_NAME_LEN = 80


def _serialize_category(c: DashboardCategory) -> dict:
    return {
        "id": str(c.id),
        "name": c.name,
        "order_index": c.order_index,
        "created_by": str(c.created_by_id) if c.created_by_id else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


def _serialize_widget(w: DashboardCustomWidget) -> dict:
    return {
        "id": str(w.id),
        "slot_id": w.slot_id(),
        "title": w.title,
        "subtitle": w.subtitle or "",
        "link_url": w.link_url or "",
        "icon": w.icon or "sparkles",
        "category_id": str(w.category_id) if w.category_id else None,
        "user_id": str(w.user_id),
        "created_at": w.created_at.isoformat() if w.created_at else None,
        "updated_at": w.updated_at.isoformat() if w.updated_at else None,
    }


def _parse_uuid(raw) -> uuid.UUID | None:
    if raw in (None, "", b""):
        return None
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        return None


def _clean_name(raw) -> str:
    return (str(raw or "").strip())[:_MAX_CATEGORY_NAME_LEN]


class DashboardCategoryListCreateView(APIView):
    """
    GET  /api/dashboard/categories/         -> list tenant categories (any auth user in tenant).
    POST /api/dashboard/categories/         -> create (manager only).
      body: { name: str, order_index?: int }
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        if not getattr(user, "restaurant_id", None):
            return Response({"categories": []})
        qs = DashboardCategory.objects.filter(restaurant_id=user.restaurant_id).order_by(
            "order_index", "name"
        )
        return Response({"categories": [_serialize_category(c) for c in qs]})

    def post(self, request):
        user = request.user
        if not _can_customize_dashboard(user):
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        if not getattr(user, "restaurant_id", None):
            return Response({"detail": "User has no workspace"}, status=status.HTTP_400_BAD_REQUEST)

        body = request.data or {}
        name = _clean_name(body.get("name"))
        if not name:
            return Response({"detail": "name is required"}, status=status.HTTP_400_BAD_REQUEST)

        order_raw = body.get("order_index")
        try:
            order_index = int(order_raw) if order_raw is not None else 0
        except (TypeError, ValueError):
            order_index = 0

        try:
            c = DashboardCategory.objects.create(
                restaurant_id=user.restaurant_id,
                name=name,
                order_index=order_index,
                created_by=user,
            )
        except IntegrityError:
            return Response(
                {"detail": "A category with that name already exists"},
                status=status.HTTP_409_CONFLICT,
            )

        return Response(
            {"category": _serialize_category(c)}, status=status.HTTP_201_CREATED
        )


class DashboardCategoryDetailView(APIView):
    """
    PATCH  /api/dashboard/categories/<uuid>/   -> rename / reorder (manager only).
      body: { name?: str, order_index?: int }
    DELETE /api/dashboard/categories/<uuid>/   -> delete (manager only). Widgets keep
      their rows but become uncategorized (FK SET_NULL).
    """

    permission_classes = [permissions.IsAuthenticated]

    def _get_own(self, request, pk):
        user = request.user
        if not getattr(user, "restaurant_id", None):
            return None
        return DashboardCategory.objects.filter(
            id=pk, restaurant_id=user.restaurant_id
        ).first()

    def patch(self, request, pk):
        user = request.user
        if not _can_customize_dashboard(user):
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        c = self._get_own(request, pk)
        if c is None:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        body = request.data or {}
        changed: list[str] = []
        if "name" in body:
            new_name = _clean_name(body.get("name"))
            if not new_name:
                return Response({"detail": "name cannot be empty"}, status=status.HTTP_400_BAD_REQUEST)
            if new_name != c.name:
                c.name = new_name
                changed.append("name")
        if "order_index" in body:
            try:
                c.order_index = int(body.get("order_index") or 0)
                changed.append("order_index")
            except (TypeError, ValueError):
                return Response({"detail": "order_index must be int"}, status=status.HTTP_400_BAD_REQUEST)

        if changed:
            try:
                c.save(update_fields=changed + ["updated_at"])
            except IntegrityError:
                return Response(
                    {"detail": "A category with that name already exists"},
                    status=status.HTTP_409_CONFLICT,
                )
        return Response({"category": _serialize_category(c)})

    def delete(self, request, pk):
        user = request.user
        if not _can_customize_dashboard(user):
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        c = self._get_own(request, pk)
        if c is None:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        c.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class DashboardCustomWidgetCreateView(APIView):
    """
    POST /api/dashboard/custom-widgets/       -> create widget for current user (manager only).
      body: {
        title: str, subtitle?: str, link_url?: str, icon?: str,
        category_id?: uuid, add_to_dashboard?: bool (default true)
      }
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user
        if not _can_customize_dashboard(user):
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        if not getattr(user, "restaurant_id", None):
            return Response({"detail": "User has no workspace"}, status=status.HTTP_400_BAD_REQUEST)

        data = request.data or {}
        title = str(data.get("title") or "").strip()
        if not title:
            return Response({"detail": "title is required"}, status=status.HTTP_400_BAD_REQUEST)
        subtitle = str(data.get("subtitle") or "")[:2000]
        link_raw = str(data.get("link_url") or data.get("link") or "")
        link_url = ensure_link(title, link_raw)
        icon_raw = (str(data.get("icon") or "sparkles")).strip().lower()[:64]
        if icon_raw not in ALLOWED_CUSTOM_WIDGET_ICONS:
            icon_raw = "sparkles"

        category_id = _parse_uuid(data.get("category_id"))
        category = None
        if category_id is not None:
            category = DashboardCategory.objects.filter(
                id=category_id, restaurant_id=user.restaurant_id
            ).first()
            if category is None:
                return Response({"detail": "category_id not found"}, status=status.HTTP_400_BAD_REQUEST)

        add_to_dashboard = data.get("add_to_dashboard", True)
        if isinstance(add_to_dashboard, str):
            add_to_dashboard = add_to_dashboard.lower() in ("1", "true", "yes")

        w = DashboardCustomWidget.objects.create(
            user=user,
            restaurant_id=user.restaurant_id,
            category=category,
            title=title[:255],
            subtitle=subtitle,
            link_url=link_url,
            icon=icon_raw,
        )

        if add_to_dashboard:
            current = _clean_order(user.dashboard_widget_order, user)
            if current is None:
                current = list(DEFAULT_DASHBOARD_WIDGET_ORDER)
            slot = w.slot_id()
            if slot not in current:
                current.append(slot)
            user.dashboard_widget_order = current
            user.save(update_fields=["dashboard_widget_order"])

        return Response(
            {"widget": _serialize_widget(w)}, status=status.HTTP_201_CREATED
        )


class DashboardCustomWidgetDetailView(APIView):
    """
    PATCH  /api/dashboard/custom-widgets/<uuid>/  -> update (owner or manager in same tenant).
      body: any of { title, subtitle, link_url, icon, category_id }
    DELETE /api/dashboard/custom-widgets/<uuid>/  -> delete (owner or manager in same tenant).
      Also removes the `custom:<uuid>` slot from the user's saved layout.
    """

    permission_classes = [permissions.IsAuthenticated]

    def _get_widget(self, request, pk):
        user = request.user
        if not getattr(user, "restaurant_id", None):
            return None
        w = DashboardCustomWidget.objects.filter(
            id=pk, restaurant_id=user.restaurant_id
        ).first()
        if w is None:
            return None
        if w.user_id == user.id:
            return w
        if _can_customize_dashboard(user):
            return w
        return None

    def patch(self, request, pk):
        w = self._get_widget(request, pk)
        if w is None:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        body = request.data or {}
        updates: list[str] = []

        title_changed = False
        if "title" in body:
            title = str(body.get("title") or "").strip()
            if not title:
                return Response({"detail": "title cannot be empty"}, status=status.HTTP_400_BAD_REQUEST)
            if title[:255] != w.title:
                w.title = title[:255]
                updates.append("title")
                title_changed = True
        if "subtitle" in body:
            w.subtitle = str(body.get("subtitle") or "")[:2000]
            updates.append("subtitle")
        if "link_url" in body or "link" in body:
            w.link_url = ensure_link(w.title, str(body.get("link_url") or body.get("link") or ""))
            updates.append("link_url")
        elif title_changed:
            # Re-resolve the auto-link when the title changes and caller didn't
            # supply one — keeps shortcuts pointing somewhere sensible.
            resolved = ensure_link(w.title, None)
            if resolved and resolved != w.link_url:
                w.link_url = resolved
                updates.append("link_url")
        if "icon" in body:
            icon_raw = (str(body.get("icon") or "sparkles")).strip().lower()[:64]
            if icon_raw not in ALLOWED_CUSTOM_WIDGET_ICONS:
                icon_raw = "sparkles"
            w.icon = icon_raw
            updates.append("icon")
        if "category_id" in body:
            raw = body.get("category_id")
            if raw in (None, "", "null"):
                w.category = None
                updates.append("category")
            else:
                cid = _parse_uuid(raw)
                if cid is None:
                    return Response({"detail": "category_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)
                cat = DashboardCategory.objects.filter(
                    id=cid, restaurant_id=w.restaurant_id
                ).first()
                if cat is None:
                    return Response({"detail": "category_id not found"}, status=status.HTTP_404_NOT_FOUND)
                w.category = cat
                updates.append("category")

        if updates:
            w.save(update_fields=updates + ["updated_at"])
        return Response({"widget": _serialize_widget(w)})

    def delete(self, request, pk):
        w = self._get_widget(request, pk)
        if w is None:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        slot = w.slot_id()
        owner = w.user
        w.delete()

        try:
            current = _clean_order(getattr(owner, "dashboard_widget_order", None), owner)
            if current is not None and slot in current:
                current = [s for s in current if s != slot]
                owner.dashboard_widget_order = current
                owner.save(update_fields=["dashboard_widget_order"])
        except Exception:  # pragma: no cover
            logger.exception("Failed to prune layout after deleting custom widget %s", slot)

        return Response(status=status.HTTP_204_NO_CONTENT)
