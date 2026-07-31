"""REST + agent views for tenant automations."""

from __future__ import annotations

import logging

from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action, api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from accounts.models import CustomUser
from automations.models import AutomationRunLog, TenantAutomation
from automations.serializers import (
    AutomationCatalogSerializer,
    AutomationRunLogSerializer,
    TenantAutomationSerializer,
)
from automations.services.engine import (
    build_automation_from_miya_payload,
    normalize_automation_steps,
    summarize_automation_fields,
)
from miya.services.tenant import resolve_active_tenant

logger = logging.getLogger(__name__)

MANAGER_ROLES = {"SUPER_ADMIN", "ADMIN", "OWNER", "MANAGER"}


def _restaurant_for_request(request):
    user = request.user
    if not user or not user.is_authenticated:
        return None
    rid = request.query_params.get("restaurant_id") or getattr(user, "restaurant_id", None)
    return resolve_active_tenant(user, preferred_restaurant_id=rid)


class IsManager(permissions.BasePermission):
    def has_permission(self, request, view):
        role = (getattr(request.user, "role", "") or "").upper()
        return request.user and request.user.is_authenticated and role in MANAGER_ROLES


class TenantAutomationViewSet(viewsets.ModelViewSet):
    serializer_class = TenantAutomationSerializer
    permission_classes = [permissions.IsAuthenticated, IsManager]

    def get_queryset(self):
        rest = _restaurant_for_request(self.request)
        if not rest:
            return TenantAutomation.objects.none()
        return TenantAutomation.objects.filter(restaurant=rest)

    def create(self, request, *args, **kwargs):
        data = dict(request.data)
        if data.get("template_id"):
            try:
                from automations.services.engine import build_automation_from_template

                fields = build_automation_from_template(
                    str(data["template_id"]), name=data.get("name")
                )
                for k, v in fields.items():
                    data.setdefault(k, v)
            except ValueError as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_create(self, serializer):
        rest = _restaurant_for_request(self.request)
        if not rest:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"restaurant": "No workspace linked to this account."})
        serializer.save(restaurant=rest, created_by=self.request.user)

    @action(detail=True, methods=["post"])
    def toggle(self, request, pk=None):
        auto = self.get_object()
        auto.is_active = not auto.is_active
        auto.save(update_fields=["is_active", "updated_at"])
        return Response(TenantAutomationSerializer(auto).data)

    @action(detail=False, methods=["get"])
    def catalog(self, request):
        return Response(AutomationCatalogSerializer.build())

    @action(detail=True, methods=["get"])
    def runs(self, request, pk=None):
        auto = self.get_object()
        logs = AutomationRunLog.objects.filter(automation=auto).order_by("-created_at")[:50]
        return Response(AutomationRunLogSerializer(logs, many=True).data)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_create_automation(request):
    """Miya tool endpoint — create automation from structured payload or template."""
    from scheduling.views_agent import _resolve_restaurant_for_agent

    restaurant, acting_user, err = _resolve_restaurant_for_agent(request)
    if err:
        return Response({"success": False, "error": err["error"]}, status=err["status"])

    data = request.data if isinstance(getattr(request, "data", None), dict) else {}
    try:
        fields = build_automation_from_miya_payload(data)
    except ValueError as exc:
        return Response(
            {"success": False, "error": str(exc), "message_for_user": str(exc)},
            status=status.HTTP_400_BAD_REQUEST,
        )

    auto = TenantAutomation.objects.create(
        restaurant=restaurant,
        created_by=acting_user,
        name=fields["name"],
        description=fields.get("description") or "",
        trigger_type=fields["trigger_type"],
        trigger_config=fields.get("trigger_config") or {},
        steps=fields.get("steps") or [],
        template_id=fields.get("template_id") or "",
        is_active=fields.get("is_active", True),
        stop_miya_on_match=fields.get("stop_miya_on_match", False),
    )

    return Response(
        {
            "success": True,
            "automation": TenantAutomationSerializer(auto).data,
            "message_for_user": (
                f"Automation '{auto.name}' is {'active' if auto.is_active else 'saved as draft'}."
            ),
            "automation_summary": summarize_automation_fields(fields),
        }
    )


@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_list_automations(request):
    from scheduling.views_agent import _resolve_restaurant_for_agent

    restaurant, _, err = _resolve_restaurant_for_agent(request)
    if err:
        return Response({"success": False, "error": err["error"]}, status=err["status"])

    qs = TenantAutomation.objects.filter(restaurant=restaurant).order_by("-updated_at")[:50]
    return Response(
        {
            "success": True,
            "count": qs.count(),
            "automations": TenantAutomationSerializer(qs, many=True).data,
        }
    )
