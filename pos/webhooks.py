from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework.views import APIView
from .integrations import IntegrationManager
from .models import Order
import json
from django.conf import settings
from .tasks import process_square_webhook_event, verify_square_webhook_signature
from accounts.models import Restaurant


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
    """Handle Toast webhooks"""
    permission_classes = []
    
    def post(self, request):
        try:
            payload = json.loads(request.body)
            event_type = payload.get('eventType')
            
            # Handle different Toast events
            if event_type == 'ORDER_CREATED':
                # Process new order from Toast
                pass
            elif event_type == 'PAYMENT_PROCESSED':
                # Process payment confirmation
                pass
            
            return Response({'status': 'received'})
        except Exception as e:
            return Response({'error': str(e)}, status=400)


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
    """Handle Clover webhooks"""
    permission_classes = []
    
    def post(self, request):
        try:
            payload = json.loads(request.body)
            object_type = payload.get('objectType')
            
            # Handle different Clover events
            if object_type == 'ORDER':
                # Process order event
                pass
            elif object_type == 'PAYMENT':
                # Process payment event
                pass
            
            return Response({'status': 'received'})
        except Exception as e:
            return Response({'error': str(e)}, status=400)
