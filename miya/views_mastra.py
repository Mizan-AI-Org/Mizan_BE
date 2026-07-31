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
from core.read_through_cache import get_or_set, safe_cache_get, safe_cache_set

from .cache_keys import mastra_tool_key, whatsapp_context_key
from .cache_policy import mastra_read_cache_ttl, whatsapp_context_cache_ttl
from .services.tools import execute_tool, tools_for_user
from .services.user_errors import pick_user_message
from .services.context import build_session_context, build_system_prompt
from .services.whatsapp_identity import (
    normalize_whatsapp_phone,
    resolve_whatsapp_user,
    whatsapp_session_hint,
)

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

    def _run_tool() -> dict:
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
        return result if isinstance(result, dict) else {"success": True, "data": result}

    cache_ttl = mastra_read_cache_ttl(tool_name)
    try:
        if cache_ttl:
            cache_key = mastra_tool_key(
                tool_name=tool_name,
                arguments=arguments,
                user_id=str(user.id) if user else str(user_id or ""),
                restaurant_id=str(restaurant_id or ""),
                channel=channel,
            )
            cached = safe_cache_get(cache_key)
            if cached is not None:
                return Response(cached)
            result = _run_tool()
            if isinstance(result, dict) and result.get("success") is not False:
                safe_cache_set(cache_key, result, cache_ttl)
            return Response(result)
        result = _run_tool()
        return Response(result)
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


def _build_whatsapp_context_payload(phone_digits: str) -> dict:
    user, session = resolve_whatsapp_user(phone_digits)
    if not user:
        return {
            "success": True,
            "can_use_miya": False,
            "message_for_user": (
                "Please ask your manager for a Mizan invite link so I can recognize your number."
            ),
        }

    if not user_can_use_miya(user):
        return {
            "success": True,
            "can_use_miya": False,
            "message_for_user": (
                "Your role doesn't have Miya access for this workspace. Contact your manager."
            ),
        }

    hint = whatsapp_session_hint(session, phone_digits)
    session_ctx = build_session_context(user, channel="whatsapp", session_hint=hint)
    system_prompt = build_system_prompt(user, channel="whatsapp", session_hint=hint)

    return {
        "success": True,
        "can_use_miya": True,
        "mizan": {
            "restaurantId": session_ctx.get("restaurant_id"),
            "userId": session_ctx.get("user_id"),
            "role": session_ctx.get("role"),
            "channel": "whatsapp",
            "systemPrompt": system_prompt[:12000],
            "phone": phone_digits,
            "userName": session_ctx.get("user_name"),
            "restaurantName": session_ctx.get("restaurant_name"),
            "businessVertical": session_ctx.get("business_vertical"),
        },
    }


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def mastra_whatsapp_context(request):
    """
    Resolve WhatsApp phone → tenant context for Mastra channel handler.
    Auth: Bearer MIYA_MASTRA_API_KEY or LUA_WEBHOOK_API_KEY.
    """
    if not _validate_mastra_or_agent_key(request):
        return Response({"success": False, "error": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    data = request.data if isinstance(request.data, dict) else {}
    phone_raw = (data.get("phone") or data.get("phone_digits") or data.get("from") or "").strip()
    phone_digits = normalize_whatsapp_phone(phone_raw)
    if len(phone_digits) < 6:
        return Response(
            {"success": False, "error": "phone required", "message_for_user": "Invalid phone number."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    ttl = whatsapp_context_cache_ttl()
    payload = get_or_set(whatsapp_context_key(phone_digits), ttl, lambda: _build_whatsapp_context_payload(phone_digits))
    return Response(payload)
