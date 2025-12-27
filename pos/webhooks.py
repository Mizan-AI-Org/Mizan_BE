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
    
    def post(self, request):
        try:
            payload = json.loads(request.body)
            event_type = payload.get('type')
            
            # Handle different Square events
            if event_type == 'order.created':
                # Process new order from Square
                pass
            elif event_type == 'payment.updated':
                # Process payment update
                pass
            
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
