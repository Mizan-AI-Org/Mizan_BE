"""Mastra integration endpoints — tool dispatch for the TypeScript Miya agent."""

from __future__ import annotations

import logging

from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from accounts.models import CustomUser
from accounts.rbac_enforce import user_can_use_miya

from .services.tools import execute_tool, tools_for_user
from .services.user_errors import pick_user_message

logger = logging.getLogger(__name__)


def _validate_mastra_or_agent_key(request) -> bool:
    expected = (getattr(settings, "MIYA_MASTRA_API_KEY", None) or "").strip()
    if not expected:
        expected = (getattr(settings, "LUA_WEBHOOK_API_KEY", None) or "").strip()
    if not expected:
        return False
    auth = (request.META.get("HTTP_AUTHORIZATION") or "").strip()
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        return token == expected
    return False


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def mastra_execute_tool(request):
    """
    Execute a Mizan tool on behalf of the Mastra Miya agent.
    Auth: Bearer MIYA_MASTRA_API_KEY or LUA_WEBHOOK_API_KEY, or user JWT.
    """
    if not _validate_mastra_or_agent_key(request):
        jwt_user = getattr(request, "user", None)
        if not jwt_user or not jwt_user.is_authenticated:
            return Response({"success": False, "error": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    data = request.data if isinstance(request.data, dict) else {}
    tool_name = (data.get("tool_name") or "").strip()
    arguments = data.get("arguments") if isinstance(data.get("arguments"), dict) else {}
    user_id = data.get("user_id")
    channel = (data.get("channel") or "dashboard").strip()

    if not tool_name:
        return Response({"success": False, "error": "tool_name is required"}, status=status.HTTP_400_BAD_REQUEST)

    user = None
    if user_id:
        user = CustomUser.objects.filter(id=user_id, is_active=True).first()
    elif getattr(request, "user", None) and request.user.is_authenticated:
        user = request.user

    if user and not user_can_use_miya(user):
        return Response({"success": False, "error": "Miya access denied for this user"}, status=status.HTTP_403_FORBIDDEN)

    restaurant_id = (
        arguments.get("restaurant_id")
        or request.META.get("HTTP_X_RESTAURANT_ID")
        or (str(user.restaurant_id) if user and user.restaurant_id else None)
    )

    session_context = {
        "restaurant_id": restaurant_id,
        "user_id": str(user.id) if user else user_id,
        "user_phone": getattr(user, "phone", None),
        "role": getattr(user, "role", None),
        "channel": channel,
    }

    access_token = None
    auth = (request.META.get("HTTP_AUTHORIZATION") or "").strip()
    if auth.lower().startswith("bearer ") and user:
        token = auth[7:].strip()
        agent_key = (getattr(settings, "LUA_WEBHOOK_API_KEY", None) or "").strip()
        if token != agent_key:
            access_token = token

    try:
        result = execute_tool(
            tool_name,
            arguments,
            access_token=access_token,
            session_context=session_context,
            user=user,
        )
        if isinstance(result, dict) and result.get("success") is False:
            msg = pick_user_message(result)
            result.setdefault("message_for_user", msg)
        return Response(result if isinstance(result, dict) else {"success": True, "data": result})
    except Exception as exc:
        logger.exception("mastra_execute_tool failed for %s", tool_name)
        return Response(
            {"success": False, "error": str(exc)[:300]},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def mastra_tools_catalog(request):
    """OpenAI-style tool schemas for Mastra bootstrap (optional)."""
    if not _validate_mastra_or_agent_key(request):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return Response({"error": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    from .services.tools import TOOL_SCHEMAS, tools_for_user

    user = getattr(request, "user", None)
    if user and user.is_authenticated:
        schemas = tools_for_user(user)
    else:
        schemas = TOOL_SCHEMAS
    names = [
        (s.get("function") or {}).get("name")
        for s in schemas
        if isinstance(s, dict)
    ]
    return Response({"tools": schemas, "count": len([n for n in names if n])})
