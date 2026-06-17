"""Export internal menu for delivery-aggregator sync (Glovo-first)."""
from __future__ import annotations

from django.utils import timezone

from menu.models import MenuItem
from payroll.models import DeliveryMenuSnapshot


def sync_delivery_menu(restaurant, provider: str = "GLOVO") -> dict:
    provider = (provider or "GLOVO").upper()
    items = MenuItem.objects.filter(restaurant=restaurant, is_active=True).select_related("category")
    payload_items = []
    for item in items:
        payload_items.append(
            {
                "id": str(item.id),
                "name": item.name,
                "description": getattr(item, "description", "") or "",
                "price": float(item.price) if item.price is not None else 0,
                "category": getattr(item.category, "name", "") if getattr(item, "category", None) else "",
                "available": bool(getattr(item, "is_active", True)),
            }
        )

    payload = {
        "provider": provider,
        "restaurant_id": str(restaurant.id),
        "restaurant_name": restaurant.name,
        "synced_at": timezone.now().isoformat(),
        "items": payload_items,
    }

    snap = DeliveryMenuSnapshot.objects.create(
        restaurant=restaurant,
        provider=DeliveryMenuSnapshot.PROVIDER_GLOVO if provider == "GLOVO" else provider,
        item_count=len(payload_items),
        payload=payload,
    )

    gs = dict(getattr(restaurant, "general_settings", None) or {})
    gs["delivery_menu_last_sync"] = {
        "provider": provider,
        "snapshot_id": str(snap.id),
        "item_count": len(payload_items),
        "synced_at": payload["synced_at"],
    }
    restaurant.general_settings = gs
    restaurant.save(update_fields=["general_settings"])

    return {
        "success": True,
        "provider": provider,
        "item_count": len(payload_items),
        "snapshot_id": str(snap.id),
        "message_for_user": (
            f"✓ Synced {len(payload_items)} menu item(s) for {provider} delivery. "
            "Export ready — connect Glovo API credentials when available."
        ),
    }
