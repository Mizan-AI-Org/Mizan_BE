"""
POS Service Layer - Business Logic for POS Operations
Handles payments, orders, receipts, and analytics
"""

from decimal import Decimal
from datetime import datetime, timedelta
from django.db import transaction
from django.db.models import Sum, Count, Q, F, Avg
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.template.loader import render_to_string

from .models import Order, OrderLineItem, Payment, POSTransaction, Discount, OrderModifier
from accounts.models import Restaurant


class PaymentService:
    """Service for handling payment operations"""
    
    @staticmethod
    def process_payment(order, payment_method, amount, tip_amount=0, reference_number=None):
        """
        Process payment for an order
        
        Args:
            order: Order instance
            payment_method: CASH, CARD, UPI, etc.
            amount: Payment amount
            tip_amount: Tip amount
            reference_number: Transaction reference
        
        Returns:
            Payment instance
        """
        if amount < order.total_amount:
            raise ValidationError("Insufficient payment amount")
        
        payment = Payment.objects.create(
            order=order,
            restaurant=order.restaurant,
            payment_method=payment_method,
            amount=amount,
            amount_paid=amount,
            tip_amount=tip_amount,
            change_given=amount - order.total_amount,
            status='COMPLETED',
            transaction_id=reference_number,
            processed_by=None  # Will be set by view
        )
        
        # Update order status
        order.status = 'COMPLETED'
        order.completion_time = timezone.now()
        order.save()
        
        # Release table if dine-in
        if order.table:
            order.table.status = 'AVAILABLE'
            order.table.save()
        
        # Create transaction log
        POSTransaction.objects.create(
            restaurant=order.restaurant,
            order=order,
            transaction_type='PAYMENT_PROCESSED',
            description=f'Payment processed via {payment_method}',
            amount_involved=amount
        )
        
        return payment
    
    @staticmethod
    def refund_payment(payment, refund_amount, refund_reason):
        """
        Process refund for a payment
        
        Args:
            payment: Payment instance
            refund_amount: Amount to refund
            refund_reason: Reason for refund
        """
        if refund_amount <= 0 or refund_amount > payment.amount:
            raise ValidationError("Invalid refund amount")
        
        if refund_amount == payment.amount:
            payment.status = 'REFUNDED'
        else:
            payment.status = 'PARTIALLY_REFUNDED'
        
        payment.refund_amount = refund_amount
        payment.refund_reason = refund_reason
        payment.save()
        
        # Update order refund tracking
        order = payment.order
        order.refund_status = 'COMPLETED'
        order.refund_amount = refund_amount
        order.refund_date = timezone.now()
        order.save()
        
        # Create transaction log
        POSTransaction.objects.create(
            restaurant=payment.restaurant,
            order=order,
            transaction_type='PAYMENT_REFUNDED',
            user=None,
            description=f'Refund processed: {refund_reason}',
            amount_involved=-refund_amount
        )
        
        return payment
    
    @staticmethod
    def split_payment(order, payment_splits):
        """
        Handle split payments (multiple payment methods)
        
        Args:
            order: Order instance
            payment_splits: List of {'method': str, 'amount': Decimal}
        """
        with transaction.atomic():
            total_amount = sum(split['amount'] for split in payment_splits)
            
            if total_amount < order.total_amount:
                raise ValidationError("Total split payments less than order amount")
            
            payments = []
            for split in payment_splits:
                payment = Payment.objects.create(
                    order=order,
                    restaurant=order.restaurant,
                    payment_method=split['method'],
                    amount=split['amount'],
                    amount_paid=split['amount'],
                    status='COMPLETED'
                )
                payments.append(payment)
            
            order.status = 'COMPLETED'
            order.completion_time = timezone.now()
            order.save()
            
            if order.table:
                order.table.status = 'AVAILABLE'
                order.table.save()
            
            return payments


class OrderService:
    """Service for handling order operations"""
    
    @staticmethod
    def create_order_with_items(restaurant, order_type, table=None, items=None, server=None):
        """
        Create order with line items
        
        Args:
            restaurant: Restaurant instance
            order_type: DINE_IN, TAKEOUT, DELIVERY, CATERING
            table: Table instance (for dine-in)
            items: List of {'menu_item_id': uuid, 'quantity': int, 'special_instructions': str}
            server: User instance (server)
        """
        # Generate unique order number
        today_count = Order.objects.filter(
            restaurant=restaurant,
            order_time__date=timezone.now().date()
        ).count()
        order_number = f"ORD-{timezone.now().strftime('%Y%m%d')}-{today_count + 1:04d}"
        
        with transaction.atomic():
            order = Order.objects.create(
                restaurant=restaurant,
                order_number=order_number,
                order_type=order_type,
                table=table,
                server=server,
                status='PENDING'
            )
            
            if items:
                from menu.models import MenuItem
                for item_data in items:
                    menu_item = MenuItem.objects.get(id=item_data['menu_item_id'])
                    OrderLineItem.objects.create(
                        order=order,
                        menu_item=menu_item,
                        quantity=item_data.get('quantity', 1),
                        unit_price=menu_item.price,
                        special_instructions=item_data.get('special_instructions', '')
                    )
            
            order.calculate_total()
        
        return order
    
    @staticmethod
    def apply_discount_code(order, discount_code):
        """
        Apply discount code to order
        
        Args:
            order: Order instance
            discount_code: Discount code string
        """
        try:
            discount = Discount.objects.get(discount_code=discount_code)
        except Discount.DoesNotExist:
            raise ValidationError("Invalid discount code")
        
        # Calculate discount amount
        discount_amount = discount.calculate_discount_amount(order.subtotal)
        
        order.discount_amount = discount_amount
        order.discount_reason = f"Discount code: {discount_code}"
        order.save()
        
        # Increment usage counter
        discount.usage_count = F('usage_count') + 1
        discount.save()
        
        # Create transaction log
        POSTransaction.objects.create(
            restaurant=order.restaurant,
            order=order,
            transaction_type='DISCOUNT_APPLIED',
            description=f'Discount code {discount_code} applied',
            amount_involved=-discount_amount
        )
        
        return order
    
    @staticmethod
    def add_modifier_to_item(line_item, modifier_name, modifier_price):
        """Add modifier/extra to an order line item"""
        modifier = OrderModifier.objects.create(
            line_item=line_item,
            modifier_name=modifier_name,
            modifier_price=modifier_price
        )
        
        # Update line item total
        line_item.total_price = (line_item.quantity * line_item.unit_price) + modifier_price
        line_item.save()
        
        # Update order total
        line_item.order.calculate_total()
        
        return modifier
    
    @staticmethod
    def cancel_order(order, reason=''):
        """Cancel an order"""
        if order.status in ['COMPLETED', 'SERVED', 'CANCELLED']:
            raise ValidationError(f"Cannot cancel order with status {order.status}")
        
        order.status = 'CANCELLED'
        order.notes = reason
        order.save()
        
        # Release table
        if order.table:
            order.table.status = 'AVAILABLE'
            order.table.save()
        
        # Create transaction log
        POSTransaction.objects.create(
            restaurant=order.restaurant,
            order=order,
            transaction_type='ORDER_CANCELLED',
            description=f'Order cancelled: {reason}',
            amount_involved=-order.total_amount
        )
        
        return order
    
    @staticmethod
    def get_order_summary(order):
        """Get comprehensive order summary"""
        line_items = order.line_items.all()
        
        return {
            'order_id': str(order.id),
            'order_number': order.order_number,
            'status': order.status,
            'order_type': order.order_type,
            'table': order.table.table_number if order.table else None,
            'server': order.server.email if order.server else None,
            'items_count': line_items.count(),
            'items': [
                {
                    'menu_item': item.menu_item.name,
                    'quantity': item.quantity,
                    'unit_price': float(item.unit_price),
                    'total': float(item.total_price),
                    'modifiers': [
                        {
                            'name': m.modifier_name,
                            'price': float(m.modifier_price)
                        }
                        for m in item.modifiers.all()
                    ]
                }
                for item in line_items
            ],
            'subtotal': float(order.subtotal),
            'tax': float(order.tax_amount),
            'discount': float(order.discount_amount),
            'total': float(order.total_amount),
            'order_time': order.order_time.isoformat(),
            'duration_minutes': (timezone.now() - order.order_time).total_seconds() / 60 if order.order_time else 0
        }
    
    @staticmethod
    def estimate_preparation_time(order):
        """
        Estimate order preparation time based on complexity
        Returns estimated minutes
        """
        base_time = 5  # 5 minutes base
        
        for item in order.line_items.all():
            # Add time per item
            base_time += 3
            # Complex items take longer
            if hasattr(item.menu_item, 'preparation_time'):
                base_time += item.menu_item.preparation_time
        
        return base_time


class ReceiptService:
    """Service for receipt generation and printing"""
    
    @staticmethod
    def generate_receipt_data(order):
        """Generate receipt data for an order"""
        return {
            'restaurant_name': order.restaurant.name,
            'order_number': order.order_number,
            'order_type': order.get_order_type_display(),
            'server': order.server.first_name if order.server else 'N/A',
            'table': order.table.table_number if order.table else 'N/A',
            'items': [
                {
                    'name': item.menu_item.name,
                    'qty': item.quantity,
                    'price': float(item.unit_price),
                    'total': float(item.total_price),
                    'modifiers': [
                        {'name': m.modifier_name, 'price': float(m.modifier_price)}
                        for m in item.modifiers.all()
                    ]
                }
                for item in order.line_items.all()
            ],
            'subtotal': float(order.subtotal),
            'tax_amount': float(order.tax_amount),
            'discount_amount': float(order.discount_amount),
            'total': float(order.total_amount),
            'order_time': order.order_time.strftime('%Y-%m-%d %H:%M:%S'),
            'payment_method': order.payment.payment_method if hasattr(order, 'payment') else 'N/A',
        }


class POSAnalyticsService:
    """Service for POS analytics and reporting"""
    
    @staticmethod
    def get_daily_revenue(restaurant, date=None):
        """Get daily revenue"""
        if date is None:
            date = timezone.now().date()
        
        orders = Order.objects.filter(
            restaurant=restaurant,
            order_time__date=date,
            status__in=['COMPLETED', 'SERVED']
        )
        
        revenue = orders.aggregate(Sum('total_amount'))['total_amount__sum'] or 0
        
        return {
            'date': date.isoformat(),
            'total_revenue': float(revenue),
            'total_orders': orders.count(),
            'average_order_value': float(revenue / orders.count()) if orders.count() > 0 else 0,
            'total_discounts': float(orders.aggregate(Sum('discount_amount'))['discount_amount__sum'] or 0),
            'total_tax': float(orders.aggregate(Sum('tax_amount'))['tax_amount__sum'] or 0),
        }
    
    @staticmethod
    def get_top_items(restaurant, days=7, limit=10):
        """Get top selling items"""
        start_date = timezone.now().date() - timedelta(days=days)
        
        items = OrderLineItem.objects.filter(
            order__restaurant=restaurant,
            order__order_time__date__gte=start_date,
            order__status__in=['COMPLETED', 'SERVED']
        ).values('menu_item__name').annotate(
            total_qty=Sum('quantity'),
            total_revenue=Sum('total_price')
        ).order_by('-total_qty')[:limit]
        
        return [
            {
                'item_name': item['menu_item__name'],
                'quantity': int(item['total_qty']),
                'revenue': float(item['total_revenue'])
            }
            for item in items
        ]
    
    @staticmethod
    def get_payment_methods_report(restaurant, days=7):
        """Get payment methods breakdown"""
        start_date = timezone.now().date() - timedelta(days=days)
        
        payments = Payment.objects.filter(
            restaurant=restaurant,
            payment_time__date__gte=start_date,
            status='COMPLETED'
        ).values('payment_method').annotate(
            count=Count('id'),
            total=Sum('amount')
        )
        
        return [
            {
                'method': p['payment_method'],
                'count': p['count'],
                'total': float(p['total'])
            }
            for p in payments
        ]
    
    @staticmethod
    def get_peak_hours(restaurant, days=7):
        """Get peak business hours"""
        start_date = timezone.now().date() - timedelta(days=days)
        
        orders = Order.objects.filter(
            restaurant=restaurant,
            order_time__date__gte=start_date,
            status__in=['COMPLETED', 'SERVED']
        ).extra(select={'hour': 'EXTRACT(hour FROM order_time)'}).values('hour').annotate(
            count=Count('id'),
            revenue=Sum('total_amount')
        ).order_by('hour')
        
        return [
            {
                'hour': int(o['hour']),
                'orders': o['count'],
                'revenue': float(o['revenue'])
            }
            for o in orders
        ]