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
    result = run_connection_test(update_row=True)
    body = {
        **result,
        "config": serialize_config_for_api(request),
    }
    code = status.HTTP_200_OK if result.get("ok") else status.HTTP_400_BAD_REQUEST
    return Response(body, status=code)


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
