import base64
import hashlib
import hmac
import json
import logging
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework.views import APIView
from .integrations import IntegrationManager
from .models import Order, POSExternalEvent
from django.conf import settings
from .tasks import process_square_webhook_event, verify_square_webhook_signature
from accounts.models import Restaurant

logger = logging.getLogger(__name__)


def _constant_time_hmac_matches(expected_hex_or_b64: str, raw_body: bytes, secret: str) -> bool:
    """Return True iff any of HMAC-SHA256(secret, raw_body) encoded as hex or
    base64 matches the signature header value. Providers differ on encoding,
    so we accept whichever comparison succeeds.
    """
    if not (expected_hex_or_b64 and secret):
        return False
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    candidates = (digest.hex(), base64.b64encode(digest).decode("ascii"))
    for cand in candidates:
        if hmac.compare_digest(cand, expected_hex_or_b64):
            return True
    return False


def _record_external_event(provider: str, event_id: str, event_type: str, payload: dict, restaurant) -> bool:
    """Idempotently record a provider event for a specific tenant.

    Returns True if inserted (caller should process the event), False if we've
    already seen this ``(restaurant, provider, event_id)`` combo and should
    ignore it. Callers must resolve ``restaurant`` from the payload before
    calling this helper; events that cannot be attributed to a tenant are
    processed without deduplication (the handler should simply log them).
    """
    if not restaurant or not event_id:
        # Fail open so unattributable events are at least visible in logs.
        return True
    try:
        _, created = POSExternalEvent.objects.get_or_create(
            restaurant=restaurant,
            provider=provider,
            external_event_id=str(event_id),
            defaults={
                "event_type": event_type or "",
                "payload": payload or {},
            },
        )
        return created
    except Exception:
        logger.exception("Failed to record %s external event %s", provider, event_id)
        # Fail open — we'd rather double-process than drop a webhook.
        return True


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_menu_view(request):
    """Trigger menu sync from external POS"""
    restaurant = request.user.restaurant
    if not restaurant:
        return Response({'error': 'User not associated with a restaurant'}, status=400)
    
    result = IntegrationManager.sync_menu(restaurant)
    
    if result.get('success'):
        return Response(result)
    else:
        return Response(result, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_orders_view(request):
    """Trigger order sync from external POS"""
    restaurant = request.user.restaurant
    if not restaurant:
        return Response({'error': 'User not associated with a restaurant'}, status=400)
    
    start_date = request.data.get('start_date')
    end_date = request.data.get('end_date')
    
    result = IntegrationManager.sync_orders(restaurant, start_date, end_date)
    
    if result.get('success'):
        return Response(result)
    else:
        return Response(result, status=500)


@method_decorator(csrf_exempt, name='dispatch')
class TOASTWebhookView(APIView):
    """Handle Toast webhooks.

    Toast signs the raw body with HMAC-SHA256 and puts the digest in the
    ``toast-signature`` header. Partners configure the signing secret in
    Django settings (``TOAST_WEBHOOK_SIGNING_SECRET``). If no secret is
    set we accept the payload but log a warning so misconfiguration is
    visible in logs (never silent).

    Payload attributes Toast may include:
      * ``restaurantGuid``     — primary tenant identifier.
      * ``eventId`` (or ``guid``) — idempotency key.
      * ``eventType``          — e.g. ``ORDER_PAID``, ``MENU_UPDATED``.
    """
    permission_classes = []

    def post(self, request):
        raw_body = request.body or b""
        signature = (
            request.headers.get("toast-signature")
            or request.META.get("HTTP_TOAST_SIGNATURE")
            or ""
        )
        signing_secret = getattr(settings, "TOAST_WEBHOOK_SIGNING_SECRET", "") or ""

        if signing_secret:
            if not _constant_time_hmac_matches(signature, raw_body, signing_secret):
                logger.warning("Toast webhook signature mismatch")
                return Response({"error": "Invalid signature"}, status=401)
        else:
            logger.warning(
                "TOAST_WEBHOOK_SIGNING_SECRET is not set — accepting webhook "
                "without verification. Configure in production."
            )

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except ValueError:
            return Response({"error": "Invalid JSON"}, status=400)

        guid = payload.get("restaurantGuid") or payload.get("restaurant_guid") or ""
        restaurant = None
        if guid:
            # Toast's restaurantGuid is mirrored to `pos_merchant_id` at
            # connect time, so a plain filter works without touching the
            # encrypted envelope.
            restaurant = Restaurant.objects.filter(
                pos_provider="TOAST", pos_merchant_id=guid
            ).first()

        event_id = payload.get("eventId") or payload.get("guid") or payload.get("id")
        event_type = payload.get("eventType") or payload.get("type") or ""

        should_process = _record_external_event(
            provider="TOAST",
            event_id=event_id,
            event_type=event_type,
            payload=payload,
            restaurant=restaurant,
        )

        if not should_process:
            # Already seen — Toast retries on 2xx absence, so ack quickly.
            return Response({"status": "duplicate"})

        # At this stage we acknowledge fast and let downstream jobs
        # project the event into Mizan's own models. Ordering-sensitive
        # event types (ORDER_*) should be handled via a dedicated Celery
        # task when that worker lands; keeping the ACK generic avoids
        # Toast disabling the subscription on a slow handler.
        return Response({"status": "received"})


@method_decorator(csrf_exempt, name='dispatch')
class SquareWebhookView(APIView):
    """Handle Square webhooks"""
    permission_classes = []

    def _notification_url(self, restaurant_id=None) -> str:
        tmpl = getattr(settings, "SQUARE_WEBHOOK_NOTIFICATION_URL_TEMPLATE", "") or ""
        if tmpl and restaurant_id:
            try:
                return tmpl.format(restaurant_id=str(restaurant_id))
            except Exception:
                return ""
        return getattr(settings, "SQUARE_WEBHOOK_NOTIFICATION_URL", "") or ""
    
    def post(self, request):
        try:
            raw_body = request.body or b""
            signature = request.headers.get("x-square-hmacsha256-signature") or request.META.get("HTTP_X_SQUARE_HMACSHA256_SIGNATURE") or ""
            signature_key = getattr(settings, "SQUARE_WEBHOOK_SIGNATURE_KEY", "")
            notification_url = self._notification_url()

            if not notification_url:
                return Response({'error': 'Webhook notification URL not configured'}, status=500)

            if not verify_square_webhook_signature(
                raw_body=raw_body,
                signature_header=signature,
                notification_url=notification_url,
                signature_key=signature_key,
            ):
                return Response({'error': 'Invalid signature'}, status=401)

            payload = json.loads(raw_body.decode("utf-8"))
            # Process asynchronously to keep webhook fast and resilient
            process_square_webhook_event.delay(payload)
            return Response({'status': 'received'})
        except Exception as e:
            return Response({'error': str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class SquareWebhookTenantView(SquareWebhookView):
    """Tenant-scoped Square webhook endpoint for deterministic routing.

    URL includes restaurant_id, and we also verify the event's merchant_id matches that restaurant.
    """

    def post(self, request, restaurant_id=None):
        try:
            raw_body = request.body or b""
            signature = request.headers.get("x-square-hmacsha256-signature") or request.META.get("HTTP_X_SQUARE_HMACSHA256_SIGNATURE") or ""
            signature_key = getattr(settings, "SQUARE_WEBHOOK_SIGNATURE_KEY", "")
            notification_url = self._notification_url(restaurant_id=restaurant_id)

            if not notification_url:
                return Response({'error': 'Webhook notification URL template not configured'}, status=500)

            if not verify_square_webhook_signature(
                raw_body=raw_body,
                signature_header=signature,
                notification_url=notification_url,
                signature_key=signature_key,
            ):
                return Response({'error': 'Invalid signature'}, status=401)

            payload = json.loads(raw_body.decode("utf-8"))

            # Hard isolation: ensure this restaurant exists and matches merchant_id in event payload.
            try:
                restaurant = Restaurant.objects.get(id=restaurant_id)
            except Restaurant.DoesNotExist:
                return Response({'error': 'Unknown restaurant'}, status=404)

            merchant_id = payload.get("merchant_id") or payload.get("merchantId")
            if not merchant_id:
                merchant_id = (((payload.get("data") or {}).get("object") or {}).get("merchant_id"))
            if merchant_id and restaurant.pos_merchant_id and str(merchant_id) != str(restaurant.pos_merchant_id):
                return Response({'error': 'Merchant mismatch'}, status=403)

            process_square_webhook_event.delay(payload, restaurant_id=str(restaurant.id))
            return Response({'status': 'received'})
        except Exception as e:
            return Response({'error': str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class CloverWebhookView(APIView):
    """Handle Clover webhooks.

    Clover's webhook verification has two modes:
      * **Initial verification** — Clover first POSTs a body containing
        ``{"verificationCode": "..."}``. We simply echo 200 so Clover
        registers the endpoint.
      * **Normal events** — the body has a top-level ``merchants`` map
        where keys are Clover merchant IDs. We look up the tenant by
        ``pos_merchant_id`` and record each event per-merchant.

    If ``CLOVER_WEBHOOK_SIGNING_SECRET`` is set we verify the
    ``x-clover-auth`` header as an HMAC-SHA256 of the raw body; otherwise
    we accept the webhook but log a warning (explicit rather than
    silent).
    """
    permission_classes = []

    def post(self, request):
        raw_body = request.body or b""
        signature = (
            request.headers.get("x-clover-auth")
            or request.META.get("HTTP_X_CLOVER_AUTH")
            or ""
        )
        signing_secret = getattr(settings, "CLOVER_WEBHOOK_SIGNING_SECRET", "") or ""

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except ValueError:
            return Response({"error": "Invalid JSON"}, status=400)

        # Clover's initial verification handshake is UNSIGNED by design:
        # Clover issues the signing secret after the endpoint first
        # responds 200 to a ``{"verificationCode": "..."}`` POST. So we
        # accept the handshake without HMAC validation, then enforce
        # signatures on every subsequent payload.
        if "verificationCode" in payload:
            logger.info("Clover webhook verification code received")
            return Response({"status": "verified"})

        if signing_secret:
            if not _constant_time_hmac_matches(signature, raw_body, signing_secret):
                logger.warning("Clover webhook signature mismatch")
                return Response({"error": "Invalid signature"}, status=401)
        else:
            logger.warning(
                "CLOVER_WEBHOOK_SIGNING_SECRET is not set — accepting webhook "
                "without verification. Configure in production."
            )

        merchants_map = payload.get("merchants") or {}
        if not merchants_map:
            return Response({"status": "received"})

        seen_any = False
        for merchant_id, events in merchants_map.items():
            restaurant = Restaurant.objects.filter(
                pos_provider="CLOVER", pos_merchant_id=merchant_id
            ).first()
            for ev in events or []:
                seen_any = True
                _record_external_event(
                    provider="CLOVER",
                    event_id=ev.get("objectId") or ev.get("id") or "",
                    event_type=f"{ev.get('type') or ''}:{ev.get('objectType') or ''}".strip(":"),
                    payload={"merchant_id": merchant_id, "event": ev, "ts": payload.get("ts")},
                    restaurant=restaurant,
                )

        return Response({"status": "received" if seen_any else "ignored"})
