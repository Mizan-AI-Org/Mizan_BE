import base64
import hashlib
import hmac
from typing import Any, Dict, Optional

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from accounts.models import Restaurant
from pos.integrations import IntegrationManager, SquareIntegration
from pos.models import POSExternalEvent, POSExternalObject


def _square_base_host() -> str:
    return "https://connect.squareup.com" if getattr(settings, "SQUARE_ENV", "production") == "production" else "https://connect.squareupsandbox.com"


def verify_square_webhook_signature(*, raw_body: bytes, signature_header: str, notification_url: str, signature_key: str) -> bool:
    """Verify Square webhook signature (HMAC-SHA256, base64)."""
    if not signature_header or not signature_key or not notification_url:
        return False
    msg = (notification_url or "").encode("utf-8") + (raw_body or b"")
    digest = hmac.new(signature_key.encode("utf-8"), msg, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature_header)


def _get_restaurant_for_square_payload(payload: Dict[str, Any]) -> Optional[Restaurant]:
    merchant_id = payload.get("merchant_id") or payload.get("merchantId")
    if not merchant_id:
        # Some Square webhook variants include merchant_id inside data
        merchant_id = (((payload.get("data") or {}).get("object") or {}).get("merchant_id"))
    if not merchant_id:
        return None
    try:
        return Restaurant.objects.get(pos_provider="SQUARE", pos_merchant_id=merchant_id)
    except Restaurant.DoesNotExist:
        return None


@shared_task(bind=True, max_retries=5, default_retry_delay=30)
def process_square_webhook_event(self, payload: Dict[str, Any], restaurant_id: Optional[str] = None) -> Dict[str, Any]:
    """Persist Square webhook event and upsert related objects; trigger sync jobs when needed."""
    if restaurant_id:
        try:
            restaurant = Restaurant.objects.get(id=restaurant_id)
        except Restaurant.DoesNotExist:
            return {"success": False, "error": "restaurant_not_found"}
        # Extra safety: ensure webhook merchant_id matches this restaurant's merchant_id
        mid = (payload or {}).get("merchant_id") or (payload or {}).get("merchantId")
        if not mid:
            mid = (((payload or {}).get("data") or {}).get("object") or {}).get("merchant_id")
        if mid and getattr(restaurant, "pos_merchant_id", None) and str(mid) != str(restaurant.pos_merchant_id):
            return {"success": False, "error": "merchant_mismatch"}
    else:
        restaurant = _get_restaurant_for_square_payload(payload or {})
    if not restaurant:
        return {"success": False, "error": "restaurant_not_found"}

    event_id = payload.get("event_id") or payload.get("eventId") or payload.get("id") or ""
    event_type = payload.get("type") or ""
    if not event_id:
        # Fall back to hashing the payload for idempotency
        event_id = hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()

    # Idempotent event insert
    try:
        POSExternalEvent.objects.create(
            restaurant=restaurant,
            provider="SQUARE",
            external_event_id=event_id,
            event_type=event_type,
            payload=payload or {},
            received_at=timezone.now(),
        )
    except Exception:
        # Already processed or DB transient; proceed safely
        pass

    # Upsert object snapshots when present
    data = payload.get("data") or {}
    obj_type = data.get("type") or ""
    obj_id = data.get("id") or ""
    obj = (data.get("object") or {}) if isinstance(data.get("object"), dict) else {}

    if obj_type and obj_id:
        try:
            POSExternalObject.objects.update_or_create(
                restaurant=restaurant,
                provider="SQUARE",
                object_type=obj_type,
                object_id=obj_id,
                defaults={"payload": obj or payload},
            )
        except Exception:
            pass

    # Trigger sync for catalog/menu changes
    if isinstance(event_type, str) and event_type.startswith("catalog."):
        try:
            IntegrationManager.sync_menu(restaurant)
        except Exception:
            pass

    # For order/payment events, ensure we have latest representation (best-effort)
    try:
        if obj_type in ("order", "payment") and obj_id:
            integ = SquareIntegration(restaurant)
            if obj_type == "order":
                resp = integ._request("GET", f"/orders/{obj_id}")  # noqa: SLF001
                order_obj = (resp.json() or {}).get("order") or {}
                POSExternalObject.objects.update_or_create(
                    restaurant=restaurant,
                    provider="SQUARE",
                    object_type="order",
                    object_id=obj_id,
                    defaults={"payload": order_obj},
                )
            elif obj_type == "payment":
                resp = integ._request("GET", f"/payments/{obj_id}")  # noqa: SLF001
                pay_obj = (resp.json() or {}).get("payment") or {}
                POSExternalObject.objects.update_or_create(
                    restaurant=restaurant,
                    provider="SQUARE",
                    object_type="payment",
                    object_id=obj_id,
                    defaults={"payload": pay_obj},
                )
    except Exception as e:
        # transient errors: retry (rate limits etc)
        raise self.retry(exc=e)

    return {"success": True}


@shared_task
def sync_square_menu_for_restaurant(restaurant_id: str) -> Dict[str, Any]:
    try:
        restaurant = Restaurant.objects.get(id=restaurant_id)
    except Restaurant.DoesNotExist:
        return {"success": False, "error": "restaurant_not_found"}
    if restaurant.pos_provider != "SQUARE":
        return {"success": False, "error": "not_square"}
    return IntegrationManager.sync_menu(restaurant)


@shared_task
def sync_square_orders_for_restaurant(restaurant_id: str) -> Dict[str, Any]:
    try:
        restaurant = Restaurant.objects.get(id=restaurant_id)
    except Restaurant.DoesNotExist:
        return {"success": False, "error": "restaurant_not_found"}
    if restaurant.pos_provider != "SQUARE":
        return {"success": False, "error": "not_square"}
    result = IntegrationManager.sync_orders(restaurant)
    return result

