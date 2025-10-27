from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from django.db.models import Sum, Count, Q
from django.utils import timezone
from datetime import timedelta

from .models import Table, Order, OrderLineItem, Payment, POSTransaction, ReceiptSetting, Discount, OrderModifier
from .serializers import (
    TableSerializer, OrderSerializer, OrderLineItemSerializer,
    PaymentSerializer, POSTransactionSerializer, ReceiptSettingSerializer,
    OrderCreateSerializer, DiscountSerializer, OrderModifierSerializer, OrderLineItemDetailedSerializer
)
from .services import PaymentService, OrderService, ReceiptService, POSAnalyticsService
from core.permissions import IsRestaurantOwnerOrManager


class TableViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing dining tables
    
    Endpoints:
    - GET /api/pos/tables/ - List all tables
    - POST /api/pos/tables/ - Create new table
    - GET /api/pos/tables/{id}/ - Get table details
    - PUT /api/pos/tables/{id}/ - Update table
    - DELETE /api/pos/tables/{id}/ - Delete table
    - POST /api/pos/tables/{id}/set_status/ - Update table status
    """
    serializer_class = TableSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['status', 'section', 'is_active']
    ordering_fields = ['table_number', 'status']
    ordering = ['table_number']
    
    def get_queryset(self):
        user = self.request.user
        if hasattr(user, 'restaurant') and user.restaurant:
            return Table.objects.filter(restaurant=user.restaurant)
        return Table.objects.none()
    
    def perform_create(self, serializer):
        user = self.request.user
        if hasattr(user, 'restaurant') and user.restaurant:
            serializer.save(restaurant=user.restaurant)
    
    @action(detail=True, methods=['post'])
    def set_status(self, request, pk=None):
        """Change table status"""
        table = self.get_object()
        new_status = request.data.get('status')
        
        if new_status not in dict(Table.STATUS_CHOICES):
            return Response(
                {'error': 'Invalid status'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        table.status = new_status
        table.save()
        
        return Response(
            TableSerializer(table).data,
            status=status.HTTP_200_OK
        )
    
    @action(detail=False, methods=['get'])
    def statistics(self, request):
        """Get table statistics"""
        tables = self.get_queryset()
        stats = {
            'total_tables': tables.count(),
            'available': tables.filter(status='AVAILABLE').count(),
            'occupied': tables.filter(status='OCCUPIED').count(),
            'reserved': tables.filter(status='RESERVED').count(),
            'maintenance': tables.filter(status='MAINTENANCE').count(),
            'occupancy_rate': round(
                (tables.filter(status='OCCUPIED').count() / tables.count() * 100)
                if tables.count() > 0 else 0,
                2
            )
        }
        return Response(stats)


class OrderLineItemViewSet(viewsets.ModelViewSet):
    """ViewSet for managing order line items"""
    serializer_class = OrderLineItemSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        if hasattr(user, 'restaurant') and user.restaurant:
            return OrderLineItem.objects.filter(order__restaurant=user.restaurant)
        return OrderLineItem.objects.none()
    
    @action(detail=True, methods=['post'])
    def update_status(self, request, pk=None):
        """Update line item status"""
        item = self.get_object()
        new_status = request.data.get('status')
        
        if new_status not in dict(OrderLineItem._meta.get_field('status').choices):
            return Response(
                {'error': 'Invalid status'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        item.status = new_status
        item.save()
        
        return Response(OrderLineItemSerializer(item).data)


class PaymentViewSet(viewsets.ModelViewSet):
    """ViewSet for managing payments"""
    serializer_class = PaymentSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['payment_method', 'status']
    ordering_fields = ['payment_time']
    ordering = ['-payment_time']
    
    def get_queryset(self):
        user = self.request.user
        if hasattr(user, 'restaurant') and user.restaurant:
            return Payment.objects.filter(restaurant=user.restaurant)
        return Payment.objects.none()
    
    @action(detail=True, methods=['post'])
    def process_refund(self, request, pk=None):
        """Process a refund for a payment"""
        payment = self.get_object()
        refund_amount = request.data.get('refund_amount')
        refund_reason = request.data.get('refund_reason', '')
        
        try:
            refund_amount = float(refund_amount)
        except (TypeError, ValueError):
            return Response(
                {'error': 'Invalid refund amount'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if refund_amount <= 0 or refund_amount > payment.amount:
            return Response(
                {'error': 'Refund amount must be positive and not exceed payment amount'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if refund_amount == payment.amount:
            payment.status = 'REFUNDED'
        else:
            payment.status = 'PARTIALLY_REFUNDED'
        
        payment.refund_amount = refund_amount
        payment.refund_reason = refund_reason
        payment.save()
        
        return Response(PaymentSerializer(payment).data)


class OrderViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing POS orders
    
    Endpoints:
    - GET /api/pos/orders/ - List orders
    - POST /api/pos/orders/ - Create order
    - GET /api/pos/orders/{id}/ - Get order details
    - PUT /api/pos/orders/{id}/ - Update order
    - DELETE /api/pos/orders/{id}/ - Cancel order
    - POST /api/pos/orders/{id}/add_item/ - Add item to order
    - POST /api/pos/orders/{id}/remove_item/ - Remove item from order
    - POST /api/pos/orders/{id}/apply_discount/ - Apply discount
    - POST /api/pos/orders/{id}/process_payment/ - Process payment
    - POST /api/pos/orders/{id}/complete/ - Mark order as complete
    - GET /api/pos/orders/statistics/ - Get order statistics
    """
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['status', 'order_type', 'table']
    search_fields = ['order_number', 'customer_name', 'customer_phone']
    ordering_fields = ['order_time', 'total_amount', 'status']
    ordering = ['-order_time']
    
    def get_queryset(self):
        user = self.request.user
        if hasattr(user, 'restaurant') and user.restaurant:
            return Order.objects.filter(restaurant=user.restaurant)
        return Order.objects.none()
    
    def get_serializer_class(self):
        if self.action in ['create']:
            return OrderCreateSerializer
        return OrderSerializer
    
    def perform_create(self, serializer):
        user = self.request.user
        if hasattr(user, 'restaurant') and user.restaurant:
            # Generate order number
            today_orders = Order.objects.filter(
                restaurant=user.restaurant,
                order_time__date=timezone.now().date()
            ).count()
            order_number = f"ORD-{timezone.now().strftime('%Y%m%d')}-{today_orders + 1:04d}"
            
            order = serializer.save(
                restaurant=user.restaurant,
                server=user,
                order_number=order_number
            )
            
            # Create POS transaction
            POSTransaction.objects.create(
                restaurant=user.restaurant,
                order=order,
                transaction_type='ORDER_CREATED',
                user=user,
                description=f'Order {order_number} created',
                amount_involved=0
            )
    
    @action(detail=True, methods=['post'])
    def add_item(self, request, pk=None):
        """Add item to order"""
        order = self.get_object()
        
        menu_item_id = request.data.get('menu_item_id')
        quantity = request.data.get('quantity', 1)
        special_instructions = request.data.get('special_instructions', '')
        
        try:
            quantity = int(quantity)
            if quantity < 1:
                raise ValueError
        except (TypeError, ValueError):
            return Response(
                {'error': 'Invalid quantity'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            from menu.models import MenuItem
            menu_item = MenuItem.objects.get(id=menu_item_id)
        except:
            return Response(
                {'error': 'Menu item not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        line_item = OrderLineItem.objects.create(
            order=order,
            menu_item=menu_item,
            quantity=quantity,
            unit_price=menu_item.price,
            special_instructions=special_instructions
        )
        
        order.calculate_total()
        
        return Response(
            OrderLineItemSerializer(line_item).data,
            status=status.HTTP_201_CREATED
        )
    
    @action(detail=True, methods=['post'])
    def remove_item(self, request, pk=None):
        """Remove item from order"""
        order = self.get_object()
        line_item_id = request.data.get('line_item_id')
        
        try:
            line_item = OrderLineItem.objects.get(id=line_item_id, order=order)
            line_item.delete()
            order.calculate_total()
            return Response(
                OrderSerializer(order).data,
                status=status.HTTP_200_OK
            )
        except OrderLineItem.DoesNotExist:
            return Response(
                {'error': 'Line item not found'},
                status=status.HTTP_404_NOT_FOUND
            )
    
    @action(detail=True, methods=['post'])
    def apply_discount(self, request, pk=None):
        """Apply discount to order"""
        order = self.get_object()
        discount_amount = request.data.get('discount_amount')
        discount_reason = request.data.get('discount_reason', '')
        
        try:
            discount_amount = float(discount_amount)
        except (TypeError, ValueError):
            return Response(
                {'error': 'Invalid discount amount'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if discount_amount < 0 or discount_amount > order.subtotal:
            return Response(
                {'error': 'Discount must be between 0 and subtotal'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        order.discount_amount = discount_amount
        order.discount_reason = discount_reason
        order.calculate_total()
        
        POSTransaction.objects.create(
            restaurant=order.restaurant,
            order=order,
            transaction_type='DISCOUNT_APPLIED',
            user=request.user,
            description=f'Discount applied: {discount_reason}',
            amount_involved=-discount_amount
        )
        
        return Response(OrderSerializer(order).data)
    
    @action(detail=True, methods=['post'])
    def process_payment(self, request, pk=None):
        """Process payment for order"""
        order = self.get_object()
        
        payment_method = request.data.get('payment_method')
        amount = request.data.get('amount')
        tip_amount = request.data.get('tip_amount', 0)
        
        try:
            amount = float(amount)
            tip_amount = float(tip_amount)
        except (TypeError, ValueError):
            return Response(
                {'error': 'Invalid amount'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if amount < order.total_amount:
            return Response(
                {'error': 'Insufficient payment amount'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create payment
        payment = Payment.objects.create(
            order=order,
            restaurant=order.restaurant,
            payment_method=payment_method,
            amount=amount,
            amount_paid=amount,
            tip_amount=tip_amount,
            change_given=amount - order.total_amount,
            status='COMPLETED',
            processed_by=request.user
        )
        
        order.status = 'COMPLETED'
        order.completion_time = timezone.now()
        order.save()
        
        if order.table:
            order.table.status = 'AVAILABLE'
            order.table.save()
        
        POSTransaction.objects.create(
            restaurant=order.restaurant,
            order=order,
            transaction_type='PAYMENT_PROCESSED',
            user=request.user,
            description=f'Payment processed: {payment_method}',
            amount_involved=amount
        )
        
        return Response(PaymentSerializer(payment).data)
    
    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        """Mark order as complete"""
        order = self.get_object()
        order.status = 'COMPLETED'
        order.completion_time = timezone.now()
        order.save()
        
        if order.table:
            order.table.status = 'AVAILABLE'
            order.table.save()
        
        return Response(OrderSerializer(order).data)
    
    @action(detail=False, methods=['get'])
    def statistics(self, request):
        """Get order statistics for today"""
        user = self.request.user
        if not (hasattr(user, 'restaurant') and user.restaurant):
            return Response(
                {'error': 'No restaurant associated'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        today = timezone.now().date()
        orders = Order.objects.filter(
            restaurant=user.restaurant,
            order_time__date=today
        )
        
        stats = {
            'total_orders': orders.count(),
            'completed_orders': orders.filter(status='COMPLETED').count(),
            'total_revenue': orders.filter(status='COMPLETED').aggregate(Sum('total_amount'))['total_amount__sum'] or 0,
            'average_order_value': orders.filter(status='COMPLETED').aggregate(Sum('total_amount'))['total_amount__sum'] / max(orders.filter(status='COMPLETED').count(), 1) if orders.filter(status='COMPLETED').exists() else 0,
            'by_type': {
                'dine_in': orders.filter(order_type='DINE_IN').count(),
                'takeout': orders.filter(order_type='TAKEOUT').count(),
                'delivery': orders.filter(order_type='DELIVERY').count(),
            },
            'pending_orders': orders.filter(status__in=['PENDING', 'CONFIRMED']).count(),
        }
        
        return Response(stats)
    
    @action(detail=True, methods=['post'])
    def refund(self, request, pk=None):
        """Initiate refund for an order"""
        order = self.get_object()
        refund_reason = request.data.get('refund_reason', '')
        
        if order.status not in ['COMPLETED', 'SERVED']:
            return Response(
                {'error': f'Cannot refund order with status {order.status}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            payment = order.payment
        except:
            return Response(
                {'error': 'No payment found for this order'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        try:
            refund_amount = order.total_amount
            payment = PaymentService.refund_payment(payment, refund_amount, refund_reason)
            
            POSTransaction.objects.create(
                restaurant=order.restaurant,
                order=order,
                transaction_type='PAYMENT_REFUNDED',
                user=request.user,
                description=f'Order refunded: {refund_reason}',
                amount_involved=-refund_amount
            )
            
            return Response(PaymentSerializer(payment).data)
        except ValidationError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['post'])
    def apply_discount_code(self, request, pk=None):
        """Apply discount code to order"""
        order = self.get_object()
        discount_code = request.data.get('discount_code')
        
        if not discount_code:
            return Response(
                {'error': 'Discount code is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            order = OrderService.apply_discount_code(order, discount_code)
            return Response(OrderSerializer(order).data)
        except ValidationError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['post'])
    def add_modifier(self, request, pk=None):
        """Add modifier to an order line item"""
        order = self.get_object()
        line_item_id = request.data.get('line_item_id')
        modifier_name = request.data.get('modifier_name')
        modifier_price = request.data.get('modifier_price', 0)
        
        try:
            line_item = OrderLineItem.objects.get(id=line_item_id, order=order)
            modifier = OrderService.add_modifier_to_item(line_item, modifier_name, modifier_price)
            return Response(OrderModifierSerializer(modifier).data, status=status.HTTP_201_CREATED)
        except OrderLineItem.DoesNotExist:
            return Response(
                {'error': 'Line item not found'},
                status=status.HTTP_404_NOT_FOUND
            )
    
    @action(detail=False, methods=['get'])
    def daily_summary(self, request):
        """Get daily order summary"""
        user = request.user
        if not (hasattr(user, 'restaurant') and user.restaurant):
            return Response(
                {'error': 'No restaurant associated'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        summary = POSAnalyticsService.get_daily_revenue(user.restaurant)
        return Response(summary)
    
    @action(detail=False, methods=['get'])
    def top_items(self, request):
        """Get top selling items"""
        user = request.user
        if not (hasattr(user, 'restaurant') and user.restaurant):
            return Response(
                {'error': 'No restaurant associated'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        days = request.query_params.get('days', 7)
        items = POSAnalyticsService.get_top_items(user.restaurant, int(days))
        return Response(items)


class DiscountViewSet(viewsets.ModelViewSet):
    """ViewSet for managing discount codes"""
    serializer_class = DiscountSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['is_active', 'discount_type']
    search_fields = ['discount_code', 'description']
    ordering_fields = ['created_at', 'discount_value', 'usage_count']
    ordering = ['-created_at']
    
    def get_queryset(self):
        user = self.request.user
        if hasattr(user, 'restaurant') and user.restaurant:
            return Discount.objects.filter(restaurant=user.restaurant)
        return Discount.objects.none()
    
    def perform_create(self, serializer):
        user = self.request.user
        if hasattr(user, 'restaurant') and user.restaurant:
            serializer.save(restaurant=user.restaurant)
    
    @action(detail=False, methods=['get'])
    def valid_codes(self, request):
        """Get all currently valid discount codes"""
        discounts = self.get_queryset().filter(is_active=True)
        valid = [d for d in discounts if d.is_valid()]
        return Response(DiscountSerializer(valid, many=True).data)


class POSTransactionViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet for viewing POS transaction audit logs"""
    serializer_class = POSTransactionSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['transaction_type']
    ordering_fields = ['created_at']
    ordering = ['-created_at']
    
    def get_queryset(self):
        user = self.request.user
        if hasattr(user, 'restaurant') and user.restaurant:
            return POSTransaction.objects.filter(restaurant=user.restaurant)
        return POSTransaction.objects.none()


class ReceiptSettingViewSet(viewsets.ModelViewSet):
    """ViewSet for managing receipt settings"""
    serializer_class = ReceiptSettingSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        if hasattr(user, 'restaurant') and user.restaurant:
            return ReceiptSetting.objects.filter(restaurant=user.restaurant)
        return ReceiptSetting.objects.none()
    
    @action(detail=False, methods=['get', 'post'])
    def my_settings(self, request):
        """Get or create receipt settings for current restaurant"""
        user = request.user
        if not (hasattr(user, 'restaurant') and user.restaurant):
            return Response(
                {'error': 'No restaurant associated'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if request.method == 'GET':
            setting, _ = ReceiptSetting.objects.get_or_create(
                restaurant=user.restaurant
            )
            return Response(ReceiptSettingSerializer(setting).data)
        
        elif request.method == 'POST':
            setting, _ = ReceiptSetting.objects.get_or_create(
                restaurant=user.restaurant
            )
            serializer = ReceiptSettingSerializer(setting, data=request.data, partial=True)
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)