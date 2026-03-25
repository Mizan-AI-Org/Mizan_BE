"""
Public POST endpoint for EatNow webhooks (no auth cookie / JWT).
Verifies X-EatNow-Signature (HMAC-SHA256 over raw body) and records deliveries.
"""
from __future__ import annotations

import logging

from django.db import IntegrityError, transaction
from django.http import HttpResponse, HttpResponseNotAllowed
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .eatnow_webhook import parse_webhook_json, verify_eatnow_signature
from .eatnow_webhook_processor import apply_eatnow_webhook_payload
from .models import EatNowWebhookDelivery, Restaurant

logger = logging.getLogger(__name__)


def _get_header(meta: dict, name: str) -> str:
    django_name = "HTTP_" + name.upper().replace("-", "_")
    v = meta.get(django_name) or meta.get(django_name.lower())
    return (v or "").strip()


def find_restaurant_by_eatnow_restaurant_id(eatnow_restaurant_id: str) -> Restaurant | None:
    rid = (eatnow_restaurant_id or "").strip()
    if not rid:
        return None
    qs = Restaurant.objects.filter(general_settings__reservation__eatnow_restaurant_id=rid)
    for r in qs.iterator():
        rsv = (r.general_settings or {}).get("reservation") or {}
        if (rsv.get("provider") or "").upper() != "EATAPP":
            continue
        if (rsv.get("eatnow_restaurant_id") or "").strip() == rid:
            return r
    return None


@csrf_exempt
@require_POST
def eatnow_webhook(request):
    raw = request.body
    sig = _get_header(request.META, "X-EatNow-Signature")
    event_header = _get_header(request.META, "X-EatNow-Event")
    delivery_header = _get_header(request.META, "X-EatNow-Delivery")

    payload, err = parse_webhook_json(raw)
    if err:
        return HttpResponse(status=400, content=err)

    eatnow_rid = ""
    if isinstance(payload.get("restaurant_id"), str):
        eatnow_rid = payload["restaurant_id"].strip()
    event_type = ""
    if isinstance(payload.get("event"), str):
        event_type = payload["event"].strip()
    if event_header and not event_type:
        event_type = event_header

    restaurant = find_restaurant_by_eatnow_restaurant_id(eatnow_rid) if eatnow_rid else None
    if not restaurant:
        logger.warning(
            "eatnow_webhook: no restaurant for eatnow_restaurant_id=%s delivery=%s",
            eatnow_rid,
            delivery_header or "?",
        )
        # Acknowledge to avoid endless retries; operator must align IDs in Settings.
        return HttpResponse(status=200, content="ok")

    sec = restaurant.get_reservation_oauth() or {}
    en = sec.get("eatnow") or {}
    secret = (en.get("webhook_secret") or "").strip()
    if not secret:
        logger.warning("eatnow_webhook: no webhook secret for restaurant %s", restaurant.id)
        return HttpResponse(status=401, content="webhook secret not configured")

    if not verify_eatnow_signature(raw, sig, secret):
        logger.warning(
            "eatnow_webhook: bad signature restaurant=%s delivery=%s",
            restaurant.id,
            delivery_header or "?",
        )
        return HttpResponse(status=401, content="invalid signature")

    delivery_id = (delivery_header or "").strip() or (
        str(payload.get("id") or "").strip() if isinstance(payload.get("id"), str) else ""
    )
    if not delivery_id:
        delivery_id = f"adhoc:{restaurant.id}:{event_type}:{hash(raw) & 0xFFFFFFFF:x}"

    try:
        with transaction.atomic():
            if EatNowWebhookDelivery.objects.filter(delivery_id=delivery_id[:255]).exists():
                return HttpResponse(status=200, content="ok")
            EatNowWebhookDelivery.objects.create(
                restaurant=restaurant,
                delivery_id=delivery_id[:255],
                event_type=event_type[:64] if event_type else "",
                payload=payload,
            )
            apply_eatnow_webhook_payload(restaurant, event_type, payload)
    except IntegrityError:
        return HttpResponse(status=200, content="ok")
    except Exception as e:
        logger.exception("eatnow_webhook: persist failed: %s", e)
        return HttpResponse(status=500, content="persist failed")

    logger.info(
        "eatnow_webhook: stored event=%s delivery=%s restaurant=%s",
        event_type,
        delivery_id,
        restaurant.id,
    )
    return HttpResponse(status=200, content="ok")
