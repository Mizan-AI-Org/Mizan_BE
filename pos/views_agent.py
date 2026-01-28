"""
Agent-specific views for POS operations.
These endpoints use LUA_WEBHOOK_API_KEY authentication instead of JWT.
"""

from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from rest_framework import status, permissions
from django.conf import settings

from core.utils import resolve_agent_restaurant_and_user
from pos.integrations import IntegrationManager
from pos.models import POSExternalObject
from pos.tasks import sync_square_menu_for_restaurant, sync_square_orders_for_restaurant


def validate_agent_key(request):
    """Validate the agent API key from Authorization header."""
    auth_header = request.headers.get("Authorization")
    expected_key = getattr(settings, "LUA_WEBHOOK_API_KEY", None)

    if not expected_key:
        return False, "Agent key not configured"

    if not auth_header or auth_header != f"Bearer {expected_key}":
        return False, "Unauthorized"

    return True, None


@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_sync_menu(request):
    """Trigger POS menu sync for the resolved restaurant (provider-agnostic)."""
    is_valid, error = validate_agent_key(request)
    if not is_valid:
        return Response({"error": error}, status=status.HTTP_401_UNAUTHORIZED)

    restaurant, _ = resolve_agent_restaurant_and_user(request=request, payload=request.data or {})
    if not restaurant:
        return Response({"error": "Unable to resolve restaurant context."}, status=status.HTTP_400_BAD_REQUEST)

    if restaurant.pos_provider == "SQUARE":
        sync_square_menu_for_restaurant.delay(str(restaurant.id))
        return Response({"success": True, "queued": True, "provider": "SQUARE"})

    # Fallback to synchronous manager for other providers
    result = IntegrationManager.sync_menu(restaurant)
    return Response(result, status=status.HTTP_200_OK if result.get("success") else status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_sync_orders(request):
    """Trigger POS order sync for the resolved restaurant (provider-agnostic)."""
    is_valid, error = validate_agent_key(request)
    if not is_valid:
        return Response({"error": error}, status=status.HTTP_401_UNAUTHORIZED)

    restaurant, _ = resolve_agent_restaurant_and_user(request=request, payload=request.data or {})
    if not restaurant:
        return Response({"error": "Unable to resolve restaurant context."}, status=status.HTTP_400_BAD_REQUEST)

    if restaurant.pos_provider == "SQUARE":
        sync_square_orders_for_restaurant.delay(str(restaurant.id))
        return Response({"success": True, "queued": True, "provider": "SQUARE"})

    result = IntegrationManager.sync_orders(restaurant)
    return Response(result, status=status.HTTP_200_OK if result.get("success") else status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_get_external_objects(request):
    """Fetch latest external POS objects (orders/payments/catalog) ingested from webhooks/sync.

    Query params:
    - provider: default "SQUARE"
    - object_type: e.g. "order", "payment"
    - limit: default 50 (max 200)
    """
    is_valid, error = validate_agent_key(request)
    if not is_valid:
        return Response({"error": error}, status=status.HTTP_401_UNAUTHORIZED)

    restaurant, _ = resolve_agent_restaurant_and_user(request=request, payload=dict(request.query_params))
    if not restaurant:
        return Response({"error": "Unable to resolve restaurant context."}, status=status.HTTP_400_BAD_REQUEST)

    provider = (request.query_params.get("provider") or "SQUARE").upper()
    object_type = request.query_params.get("object_type")
    try:
        limit = int(request.query_params.get("limit") or 50)
    except Exception:
        limit = 50
    limit = max(1, min(limit, 200))

    qs = POSExternalObject.objects.filter(restaurant=restaurant, provider=provider)
    if object_type:
        qs = qs.filter(object_type=object_type)
    qs = qs.order_by("-updated_at")[:limit]

    return Response(
        {
            "success": True,
            "provider": provider,
            "count": qs.count(),
            "objects": [
                {
                    "object_type": o.object_type,
                    "object_id": o.object_id,
                    "updated_at": o.updated_at.isoformat() if o.updated_at else None,
                    "payload": o.payload,
                }
                for o in qs
            ],
        }
    )

