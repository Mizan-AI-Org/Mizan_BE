"""Platform ops APIs for central WhatsApp / Meta configuration."""

from __future__ import annotations

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .permissions import IsPlatformOperator
from .whatsapp_services import (
    create_meta_template,
    delete_meta_template,
    disconnect_config,
    list_templates,
    run_connection_test,
    save_config,
    serialize_config_for_api,
    sync_templates_from_meta,
)
from .models import WhatsAppMessageTemplate


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated, IsPlatformOperator])
def platform_whatsapp_config(request):
    if request.method == "GET":
        return Response(serialize_config_for_api(request))

    payload = request.data if isinstance(request.data, dict) else {}
    save_config(payload, request.user)
    return Response(serialize_config_for_api(request))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsPlatformOperator])
def platform_whatsapp_test(request):
    payload = request.data if isinstance(request.data, dict) else {}
    raw_token = payload.get("access_token")
    token_override = None
    if raw_token is not None:
        token_override = str(raw_token).strip() or None
    phone_override = payload.get("phone_number_id")
    phone_override = str(phone_override).strip() if phone_override is not None else None
    waba_override = payload.get("business_account_id")
    waba_override = str(waba_override).strip() if waba_override is not None else None
    activation_override = payload.get("activation_phone")
    activation_override = str(activation_override).strip() if activation_override is not None else None
    api_override = payload.get("api_version")
    api_override = str(api_override).strip() if api_override is not None else None

    result = run_connection_test(
        update_row=True,
        phone_number_id=phone_override or None,
        business_account_id=waba_override or None,
        access_token=token_override,
        api_version=api_override or None,
        activation_phone=activation_override or None,
    )
    message = (result.get("message") or "").strip()
    body = {
        **result,
        "error": message if not result.get("ok") else "",
        "config": serialize_config_for_api(request),
    }
    return Response(body, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsPlatformOperator])
def platform_whatsapp_disconnect(request):
    disconnect_config(request.user)
    return Response(serialize_config_for_api(request))


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, IsPlatformOperator])
def platform_whatsapp_templates(request):
    if request.method == "GET":
        return Response({"results": list_templates()})

    payload = request.data if isinstance(request.data, dict) else {}
    result = create_meta_template(payload)
    if not result.get("ok"):
        return Response(result, status=status.HTTP_400_BAD_REQUEST)
    return Response({"results": list_templates(), "created": result.get("meta")}, status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsPlatformOperator])
def platform_whatsapp_templates_sync(request):
    result = sync_templates_from_meta()
    if not result.get("ok"):
        return Response(result, status=status.HTTP_400_BAD_REQUEST)
    return Response({**result, "results": list_templates()})


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsPlatformOperator])
def platform_miya_voice_preview(request):
    """Synthesize a short sample with Miya's configured voice."""
    import base64

    from notifications.services import notification_service

    payload = request.data if isinstance(request.data, dict) else {}
    text = (
        str(payload.get("text") or "").strip()
        or "Hello, I'm Miya — your AI operations companion. How can I help you today?"
    )[:500]

    audio_bytes, mime = notification_service.synthesize_speech_bytes(text)
    if not audio_bytes:
        return Response(
            {"success": False, "error": "TTS failed — check Fish Audio / OpenAI keys"},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    from miya.voice_config import serialize_miya_voice_for_api

    return Response(
        {
            "success": True,
            "mime_type": mime or "audio/mpeg",
            "base64": base64.b64encode(audio_bytes).decode("ascii"),
            "voice": serialize_miya_voice_for_api(),
        }
    )


@api_view(["DELETE"])
@permission_classes([IsAuthenticated, IsPlatformOperator])
def platform_whatsapp_template_detail(request, template_id: int):
    try:
        template = WhatsAppMessageTemplate.objects.get(pk=template_id)
    except WhatsAppMessageTemplate.DoesNotExist:
        return Response({"error": "Template not found"}, status=status.HTTP_404_NOT_FOUND)

    result = delete_meta_template(template)
    if not result.get("ok"):
        return Response(result, status=status.HTTP_400_BAD_REQUEST)
    return Response(status=status.HTTP_204_NO_CONTENT)
