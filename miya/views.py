"""Miya REST API — Fish Audio-powered AI agent for Mizan."""

from __future__ import annotations

import base64
import logging

from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from notifications.services import notification_service

from accounts.rbac_enforce import user_can_use_miya
from .services.agent import run_miya_chat
from .services.context import build_session_context, build_system_prompt

logger = logging.getLogger(__name__)

ALLOWED_ROLES = {
    "ADMIN",
    "SUPER_ADMIN",
    "MANAGER",
    "OWNER",
    "WAITER",
    "CASHIER",
    "CHEF",
    "SUPERVISOR",
}


def _miya_access_ok(user) -> bool:
    return user_can_use_miya(user)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def miya_config(request):
    """Return Miya session config for the dashboard widget."""
    user = request.user
    if user.role not in ALLOWED_ROLES and not _miya_access_ok(user):
        return Response({"enabled": False, "reason": "role_not_allowed"})

    ctx = build_session_context(user)
    fish_configured = bool(getattr(settings, "FISH_AUDIO_API_KEY", ""))

    return Response(
        {
            "enabled": True,
            "name": "Miya",
            "voice_provider": "fish-audio" if fish_configured else "openai-fallback",
            "fish_audio_configured": fish_configured,
            "session_context": ctx,
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def miya_chat(request):
    """
    POST /api/miya/chat/

    Body:
      message (required): user text
      history (optional): [{role, content}, ...]
      voice (optional bool): synthesize reply audio via Fish Audio
    """
    user = request.user
    if not _miya_access_ok(user):
        return Response(
            {"error": "Miya is not available for your role."},
            status=status.HTTP_403_FORBIDDEN,
        )

    data = request.data or {}
    message = (data.get("message") or "").strip()
    if not message:
        return Response(
            {"error": "message is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    history = data.get("history") or []
    want_voice = bool(data.get("voice"))

    auth_header = request.headers.get("Authorization", "")
    access_token = auth_header.replace("Bearer ", "").strip() if auth_header else None

    try:
        result = run_miya_chat(
            user=user,
            access_token=access_token,
            user_message=message,
            history=history,
            channel="dashboard",
        )
    except RuntimeError as exc:
        logger.exception("Miya chat failed")
        return Response(
            {"error": str(exc)},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    reply = result.get("reply") or ""
    payload = {
        "reply": reply,
        "tool_trace": result.get("tool_trace") or [],
    }

    if want_voice and reply:
        audio_bytes, mime = notification_service.synthesize_speech_bytes(reply)
        if audio_bytes:
            payload["audio"] = {
                "mime_type": mime or "audio/mpeg",
                "base64": base64.b64encode(audio_bytes).decode("ascii"),
            }

    return Response(payload)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def miya_voice(request):
    """Synthesize speech for arbitrary text using Fish Audio (Miya's voice)."""
    text = (request.data.get("text") or "").strip()
    if not text:
        return Response({"error": "text is required"}, status=status.HTTP_400_BAD_REQUEST)

    speed = float(request.data.get("speed") or 1.0)
    audio_bytes, mime = notification_service.synthesize_speech_bytes(
        text, speed=speed, fmt="mp3"
    )
    if not audio_bytes:
        return Response(
            {"error": "TTS failed — configure FISH_AUDIO_API_KEY"},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    return Response(
        {
            "mime_type": mime or "audio/mpeg",
            "base64": base64.b64encode(audio_bytes).decode("ascii"),
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def miya_instructions(request):
    """Return Miya system instructions (for debugging / client preview)."""
    return Response(
        {
            "instructions": build_system_prompt(request.user),
            "session_context": build_session_context(request.user),
        }
    )
