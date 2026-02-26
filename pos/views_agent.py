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

    from django.utils.dateparse import parse_date
    start_date = parse_date(request.data.get("start_date", "") or "") if request.data else None
    end_date = parse_date(request.data.get("end_date", "") or "") if request.data else None
    result = IntegrationManager.sync_orders(restaurant, start_date, end_date)
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


@api_view(["GET"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_get_pos_sales_summary(request):
    """Get summarized sales data for the agent."""
    is_valid, error = validate_agent_key(request)
    if not is_valid:
        return Response({"error": error}, status=status.HTTP_401_UNAUTHORIZED)

    restaurant, _ = resolve_agent_restaurant_and_user(request=request, payload=dict(request.query_params))
    if not restaurant:
        return Response({"error": "Unable to resolve restaurant context."}, status=status.HTTP_400_BAD_REQUEST)

    date_str = request.query_params.get("date")
    from django.utils.dateparse import parse_date
    date = parse_date(date_str) if date_str else None

    result = IntegrationManager.get_daily_sales_summary(restaurant, date)
    return Response(result)


@api_view(["GET"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_get_top_items(request):
    """Fetch top-selling items for the agent."""
    is_valid, error = validate_agent_key(request)
    if not is_valid:
        return Response({"error": error}, status=status.HTTP_401_UNAUTHORIZED)

    restaurant, _ = resolve_agent_restaurant_and_user(request=request, payload=dict(request.query_params))
    if not restaurant:
        return Response({"error": "Unable to resolve restaurant context."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        days = int(request.query_params.get("days") or 7)
        limit = int(request.query_params.get("limit") or 10)
    except Exception:
        days, limit = 7, 10

    result = IntegrationManager.get_top_selling_items(restaurant, days, limit)
    return Response(result)


@api_view(["GET"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_get_pos_status(request):
    """Check POS connection status for the agent."""
    is_valid, error = validate_agent_key(request)
    if not is_valid:
        return Response({"error": error}, status=status.HTTP_401_UNAUTHORIZED)

    restaurant, _ = resolve_agent_restaurant_and_user(request=request, payload=dict(request.query_params))
    if not restaurant:
        return Response({"error": "Unable to resolve restaurant context."}, status=status.HTTP_400_BAD_REQUEST)

    return Response({
        "success": True,
        "provider": restaurant.pos_provider,
        "is_connected": restaurant.pos_is_connected,
        "last_sync": restaurant.pos_token_expires_at.isoformat() if restaurant.pos_token_expires_at else None,
        "merchant_id": restaurant.pos_merchant_id,
        "location_id": restaurant.pos_location_id,
    })


@api_view(["GET"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_get_sales_analysis(request):
    """AI-powered sales analysis with trends and recommendations."""
    is_valid, error = validate_agent_key(request)
    if not is_valid:
        return Response({"error": error}, status=status.HTTP_401_UNAUTHORIZED)

    restaurant, _ = resolve_agent_restaurant_and_user(request=request, payload=dict(request.query_params))
    if not restaurant:
        return Response({"error": "Unable to resolve restaurant context."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        days = int(request.query_params.get("days") or 7)
    except Exception:
        days = 7

    result = IntegrationManager.get_sales_analysis(restaurant, days)
    return Response(result)


@api_view(["GET"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_get_prep_list(request):
    """Generate daily prep list from sales forecast + recipes + inventory."""
    is_valid, error = validate_agent_key(request)
    if not is_valid:
        return Response({"error": error}, status=status.HTTP_401_UNAUTHORIZED)

    restaurant, _ = resolve_agent_restaurant_and_user(request=request, payload=dict(request.query_params))
    if not restaurant:
        return Response({"error": "Unable to resolve restaurant context."}, status=status.HTTP_400_BAD_REQUEST)

    date_str = request.query_params.get("date")
    from django.utils.dateparse import parse_date
    target_date = parse_date(date_str) if date_str else None

    result = IntegrationManager.generate_prep_list(restaurant, target_date)
    return Response(result)

