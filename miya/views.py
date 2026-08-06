"""Miya REST API — Fish Audio-powered AI agent for Mizan."""

from __future__ import annotations

import base64
import json
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
from .services.mastra_client import mastra_enabled

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


def _should_async_miya_chat() -> bool:
    return mastra_enabled() and getattr(settings, "MIYA_ASYNC_CHAT", True)


def _chat_response_payload(result: dict, *, want_voice: bool = False) -> dict:
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
    return payload


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def miya_chat_status(request):
    """Poll async Miya dashboard chat (Celery task started by POST /api/miya/chat/)."""
    from celery.result import AsyncResult

    task_id = (request.query_params.get("task_id") or "").strip()
    if not task_id:
        return Response({"error": "task_id is required"}, status=status.HTTP_400_BAD_REQUEST)

    result = AsyncResult(task_id)
    if not result.ready():
        return Response({"status": "processing"})

    if result.failed():
        logger.warning("Miya async chat task %s failed: %s", task_id, result.result)
        return Response(
            {
                "status": "failed",
                "error": "Miya task failed",
                "reply": "Miya is temporarily unavailable. Try again shortly.",
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    data = result.result if isinstance(result.result, dict) else {}
    if data.get("error"):
        return Response(
            {
                "status": "failed",
                "error": data.get("error"),
                "reply": data.get("reply") or "Something went wrong talking to Miya.",
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    return Response({"status": "complete", **data})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def miya_config(request):
    """Return Miya session config for the dashboard widget."""
    user = request.user
    if user.role not in ALLOWED_ROLES and not _miya_access_ok(user):
        return Response({"enabled": False, "reason": "role_not_allowed"})

    from miya.voice_config import serialize_miya_voice_for_api

    ctx = build_session_context(user, preferred_restaurant_id=getattr(user, "restaurant_id", None))
    voice_meta = serialize_miya_voice_for_api()
    fish_configured = voice_meta["fish_audio_configured"]
    stt_configured = fish_configured or bool(getattr(settings, "OPENAI_API_KEY", ""))

    from .services.mastra_client import mastra_deployment_mode, mastra_enabled, mastra_health

    mastra_status = mastra_health() if mastra_enabled() else None

    return Response(
        {
            "enabled": True,
            "name": "Miya",
            "agent_provider": getattr(settings, "MIYA_AGENT_PROVIDER", "django"),
            "mastra_configured": bool(getattr(settings, "MIYA_MASTRA_URL", "")),
            "mastra_mode": mastra_deployment_mode(),
            "mastra_healthy": bool(mastra_status and mastra_status.get("ok")),
            "mastra_status": mastra_status,
            "voice_provider": voice_meta["provider"],
            "asr_provider": "fish-audio" if fish_configured else "openai-whisper",
            "fish_audio_configured": fish_configured,
            "voice": voice_meta,
            "voice_input_enabled": stt_configured,
            "attachments_enabled": True,
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
    attachment_ids = data.get("attachment_ids") or []
    if not isinstance(attachment_ids, list):
        attachment_ids = []
    attachment_ids = [str(x).strip() for x in attachment_ids if str(x).strip()]

    if not message and not attachment_ids:
        return Response(
            {"error": "message or attachment_ids is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not message and attachment_ids:
        message = "Please review the document(s) I attached and remember the details."

    history = data.get("history") or []
    want_voice = bool(data.get("voice"))
    preferred_restaurant_id = (
        data.get("restaurant_id")
        or getattr(user, "restaurant_id", None)
        or (getattr(user, "restaurant", None) and str(user.restaurant.id))
    )
    if preferred_restaurant_id:
        preferred_restaurant_id = str(preferred_restaurant_id)

    auth_header = request.headers.get("Authorization", "")
    access_token = auth_header.replace("Bearer ", "").strip() if auth_header else None

    if _should_async_miya_chat():
        from .tasks import run_miya_dashboard_chat

        task = run_miya_dashboard_chat.delay(
            user_id=str(user.id),
            user_message=message,
            history=history,
            channel="dashboard",
            preferred_restaurant_id=preferred_restaurant_id,
            access_token=access_token,
            want_voice=want_voice,
            attachment_ids=attachment_ids,
        )
        return Response(
            {"status": "processing", "task_id": task.id},
            status=status.HTTP_202_ACCEPTED,
        )

    try:
        result = run_miya_chat(
            user=user,
            access_token=access_token,
            user_message=message,
            history=history,
            channel="dashboard",
            preferred_restaurant_id=preferred_restaurant_id,
            attachment_ids=attachment_ids,
        )
    except RuntimeError as exc:
        logger.exception("Miya chat failed")
        return Response(
            {"error": str(exc)},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    return Response(_chat_response_payload(result, want_voice=want_voice))


VOICE_INPUT_ROLES = {"ADMIN", "SUPER_ADMIN", "MANAGER", "OWNER"}


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def miya_voice_chat(request):
    """
    POST /api/miya/voice-chat/

    Multipart form:
      audio (required): recorded voice clip (webm/ogg/wav)
      history (optional): JSON array of prior turns
      voice (optional): synthesize reply via Fish Audio TTS (default true)
      restaurant_id (optional): tenant scope
      language (optional): BCP-47 hint for ASR (e.g. en, fr, ar)
    """
    user = request.user
    if not _miya_access_ok(user):
        return Response(
            {"error": "Miya is not available for your role."},
            status=status.HTTP_403_FORBIDDEN,
        )
    if user.role not in VOICE_INPUT_ROLES:
        return Response(
            {"error": "Voice messages are available for managers and admins."},
            status=status.HTTP_403_FORBIDDEN,
        )

    upload = request.FILES.get("audio")
    if not upload:
        return Response(
            {"error": "audio file is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    audio_bytes = upload.read()
    if not audio_bytes:
        return Response(
            {"error": "audio file is empty"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    language = (request.data.get("language") or request.POST.get("language") or "").strip()
    if not language:
        language = "en"

    transcript = notification_service.transcribe_audio_bytes(
        audio_bytes,
        input_mime_type=getattr(upload, "content_type", None),
        language=language,
    )
    if not transcript:
        return Response(
            {"error": "Could not transcribe audio — check Fish Audio or OpenAI STT configuration."},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    history_raw = request.data.get("history") or request.POST.get("history") or "[]"
    if isinstance(history_raw, str):
        try:
            history = json.loads(history_raw) if history_raw else []
        except json.JSONDecodeError:
            history = []
    else:
        history = history_raw or []

    want_voice_raw = request.data.get("voice", request.POST.get("voice", "true"))
    want_voice = str(want_voice_raw).lower() not in ("0", "false", "no")

    preferred_restaurant_id = (
        request.data.get("restaurant_id")
        or request.POST.get("restaurant_id")
        or getattr(user, "restaurant_id", None)
        or (getattr(user, "restaurant", None) and str(user.restaurant.id))
    )
    if preferred_restaurant_id:
        preferred_restaurant_id = str(preferred_restaurant_id)

    auth_header = request.headers.get("Authorization", "")
    access_token = auth_header.replace("Bearer ", "").strip() if auth_header else None

    if _should_async_miya_chat():
        from .tasks import run_miya_dashboard_chat

        task = run_miya_dashboard_chat.delay(
            user_id=str(user.id),
            user_message=transcript,
            history=history,
            channel="dashboard",
            preferred_restaurant_id=preferred_restaurant_id,
            access_token=access_token,
            want_voice=want_voice,
        )
        return Response(
            {
                "status": "processing",
                "task_id": task.id,
                "transcript": transcript,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    try:
        result = run_miya_chat(
            user=user,
            access_token=access_token,
            user_message=transcript,
            history=history,
            channel="dashboard",
            preferred_restaurant_id=preferred_restaurant_id,
        )
    except RuntimeError as exc:
        logger.exception("Miya voice chat failed")
        return Response(
            {"error": str(exc)},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    reply = result.get("reply") or ""
    payload = {
        "transcript": transcript,
        "reply": reply,
        "tool_trace": result.get("tool_trace") or [],
        "asr_provider": "fish-audio"
        if getattr(settings, "FISH_AUDIO_API_KEY", "")
        else "openai-whisper",
    }

    if want_voice and reply:
        audio_out, mime = notification_service.synthesize_speech_bytes(reply)
        if audio_out:
            payload["audio"] = {
                "mime_type": mime or "audio/mpeg",
                "base64": base64.b64encode(audio_out).decode("ascii"),
            }
            payload["voice_provider"] = (
                "fish-audio" if getattr(settings, "FISH_AUDIO_API_KEY", "") else "openai-fallback"
            )

    return Response(payload)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def miya_voice(request):
    """Synthesize speech for arbitrary text using Fish Audio (Miya's voice)."""
    text = (request.data.get("text") or "").strip()
    if not text:
        return Response({"error": "text is required"}, status=status.HTTP_400_BAD_REQUEST)

    speed = float(request.data.get("speed") or 0) or None
    from miya.voice_config import get_miya_voice_settings

    cfg = get_miya_voice_settings()
    audio_bytes, mime = notification_service.synthesize_speech_bytes(
        text, speed=speed or cfg.speed, fmt="mp3"
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


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def miya_upload_attachment(request):
    """
    POST /api/miya/attachments/  (multipart)

    file (required): PDF, image, or office document
    caption (optional): short label or question about the file
    restaurant_id (optional): tenant scope
    """
    user = request.user
    if not _miya_access_ok(user):
        return Response(
            {"error": "Miya is not available for your role."},
            status=status.HTTP_403_FORBIDDEN,
        )

    upload = (
        request.FILES.get("file")
        or request.FILES.get("attachment")
        or request.FILES.get("document")
    )
    if not upload:
        return Response({"error": "file is required"}, status=status.HTTP_400_BAD_REQUEST)

    from miya.services.tenant import resolve_active_tenant
    from miya.services.tenant_documents import serialize_tenant_document, store_tenant_document

    preferred_restaurant_id = (
        request.data.get("restaurant_id")
        or getattr(user, "restaurant_id", None)
        or (getattr(user, "restaurant", None) and str(user.restaurant.id))
    )
    restaurant = resolve_active_tenant(user, preferred_restaurant_id=str(preferred_restaurant_id or ""))
    if not restaurant:
        return Response({"error": "No workspace linked"}, status=status.HTTP_400_BAD_REQUEST)

    file_bytes = upload.read()
    caption = (request.data.get("caption") or request.data.get("message") or "").strip()

    try:
        doc = store_tenant_document(
            restaurant=restaurant,
            uploaded_by=user,
            source="WIDGET",
            file_bytes=file_bytes,
            filename=getattr(upload, "name", "") or "upload.bin",
            mime_type=getattr(upload, "content_type", "") or "",
            caption=caption,
        )
    except ValueError as exc:
        code = str(exc)
        if code == "file_too_large":
            return Response({"error": "File too large (max 12 MB)."}, status=status.HTTP_400_BAD_REQUEST)
        if code == "unsupported_type":
            return Response({"error": "Unsupported file type."}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"error": "Could not save file."}, status=status.HTTP_400_BAD_REQUEST)
    except Exception:
        logger.exception("Miya attachment upload failed user=%s", user.id)
        return Response({"error": "Upload failed."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    row = serialize_tenant_document(doc)
    return Response(
        {
            "success": True,
            "document": row,
            "document_id": row["id"],
            "message_for_user": f"Saved {row['title']}. I will remember the details from this file.",
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
