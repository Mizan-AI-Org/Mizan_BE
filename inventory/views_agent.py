"""
Agent-authenticated inventory list for Miya.
"""
from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import InventoryItem


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def agent_list_inventory_items(request):
    """
    GET /api/inventory/agent/items/?restaurant_id=<uuid>
    Returns list of inventory items for the restaurant. Auth: Bearer LUA_WEBHOOK_API_KEY.
    """
    auth_header = request.headers.get("Authorization")
    expected_key = getattr(settings, "LUA_WEBHOOK_API_KEY", None)
    if not expected_key:
        return Response({"detail": "Agent key not configured"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    if not auth_header or auth_header != f"Bearer {expected_key}":
        return Response({"detail": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    restaurant_id = request.META.get("HTTP_X_RESTAURANT_ID") or request.query_params.get("restaurant_id")
    if not restaurant_id:
        return Response(
            {"detail": "restaurant_id or X-Restaurant-Id required."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    from accounts.models import Restaurant
    try:
        restaurant = Restaurant.objects.get(id=restaurant_id)
    except Restaurant.DoesNotExist:
        return Response({"detail": "Restaurant not found."}, status=status.HTTP_404_NOT_FOUND)

    items = InventoryItem.objects.filter(restaurant=restaurant, is_active=True).order_by("name").values(
        "id", "name", "current_stock", "unit", "reorder_level", "cost_per_unit", "last_restock_date"
    )
    data = []
    for i in items:
        d = dict(i)
        d["id"] = str(d["id"])
        if d.get("last_restock_date"):
            d["last_restock_date"] = d["last_restock_date"].isoformat()
        d["reorder_level"] = float(d["reorder_level"]) if d.get("reorder_level") is not None else None
        d["current_stock"] = float(d["current_stock"])
        d["cost_per_unit"] = float(d["cost_per_unit"])
        data.append(d)

    return Response({
        "restaurant_id": str(restaurant.id),
        "items": data,
        "count": len(data),
    })
