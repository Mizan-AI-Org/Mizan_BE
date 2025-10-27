"""
Comprehensive POS Services for Phase 3 Completion
- Payment processing & refunds
- Order management with analytics
- Discount code management
- Receipt generation
- POS Analytics & reporting
"""
import logging
from decimal import Decimal
from datetime import timedelta
from django.utils import timezone
from django.db import transaction
from django.db.models import Sum, Count, F, Q, Avg
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.template.loader import render_to_string

from .models import Order, OrderLineItem, Table, Discount, OrderModifier
from menu.models import MenuItem

logger = logging.getLogger(__name__)


class PaymentService:
    """Service for payment processing and refunds"""
    
    PAYMENT_METHODS = {
        'CASH': 'Cash',
        'CARD': 'Card',
        'MOBILE': 'Mobile Payment',
        'CHECK': 'Check',
        'GIFT_CARD': 'Gift Card',
    }
    
    @staticmethod
    @transaction.atomic
    def process_payment(order, payment_method, amount_paid, tips=Decimal('0.00'), 
                       reference_id=None, notes=None):
        """
        Process payment for an order
        
        Returns: {
            'success': bool,
            'change': Decimal,
            'payment_id': str,
            'error': str or None
        }
        """
        try:
            # Validate payment
            if amount_paid < order.total_amount:
                return {
                    'success': False,
                    'change': Decimal('0.00'),
                    'error': f'Insufficient payment. Required: {order.total_amount}, Provided: {amount_paid}',
                    'payment_id': None
                }
            
            # Calculate change
            change = amount_paid - order.total_amount
            
            # Update order
            order.payment_method = payment_method
            order.amount_paid = amount_paid
            order.change_amount = change
            order.tips = tips
            order.payment_reference_id = reference_id
            order.status = 'COMPLETED'
            order.completion_time = timezone.now()
            order.notes = notes or order.notes
            order.save()
            
            # Update table status if dine-in
            if order.order_type == 'DINE_IN' and order.table:
                order.table.status = 'AVAILABLE'
                order.table.save()
            
            logger.info(f"Payment processed for order {order.order_number}: {amount_paid}")
            
            return {
                'success': True,
                'change': change,
                'payment_id': str(order.id),
                'error': None
            }
        
        except Exception as e:
            logger.error(f"Payment processing error: {str(e)}")
            return {
                'success': False,
                'change': Decimal('0.00'),
                'error': str(e),
                'payment_id': None
            }
    
    @staticmethod
    @transaction.atomic
    def process_refund(order, refund_amount=None, refund_reason='', refund_method=None):
        """
        Process refund for an order (full or partial)
        
        Returns: {
            'success': bool,
            'refund_amount': Decimal,
            'refund_order_id': str,
            'error': str or None
        }
        """
        try:
            # Determine refund amount
            if refund_amount is None:
                refund_amount = order.total_amount
            
            if refund_amount <= 0 or refund_amount > order.total_amount:
                return {
                    'success': False,
                    'refund_amount': Decimal('0.00'),
                    'refund_order_id': None,
                    'error': f'Invalid refund amount. Max: {order.total_amount}'
                }
            
            # Check if already refunded
            if order.refund_status in ['FULL_REFUND', 'PARTIAL_REFUND']:
                existing_refund = order.refund_amount or Decimal('0.00')
                if existing_refund == order.total_amount:
                    return {
                        'success': False,
                        'refund_amount': Decimal('0.00'),
                        'refund_order_id': None,
                        'error': 'Order has already been fully refunded'
                    }
            
            # Update original order with refund tracking
            refund_type = 'FULL_REFUND' if refund_amount >= order.total_amount else 'PARTIAL_REFUND'
            order.refund_status = refund_type
            order.refund_amount = refund_amount
            order.refund_reason = refund_reason
            order.refund_date = timezone.now()
            order.save()
            
            # Create refund order record
            refund_order = Order.objects.create(
                restaurant=order.restaurant,
                order_number=f"REF-{order.order_number}",
                order_type=order.order_type,
                status='COMPLETED',
                subtotal=-refund_amount,
                tax_amount=Decimal('0.00'),
                discount_amount=Decimal('0.00'),
                total_amount=-refund_amount,
                customer_name=order.customer_name,
                customer_email=order.customer_email,
                customer_phone=order.customer_phone,
                source_order=order,
                payment_method=refund_method or order.payment_method,
                completion_time=timezone.now(),
                notes=f"Refund for {order.order_number}: {refund_reason}"
            )
            
            logger.info(f"Refund processed for order {order.order_number}: {refund_amount}")
            
            return {
                'success': True,
                'refund_amount': refund_amount,
                'refund_order_id': str(refund_order.id),
                'error': None
            }
        
        except Exception as e:
            logger.error(f"Refund processing error: {str(e)}")
            return {
                'success': False,
                'refund_amount': Decimal('0.00'),
                'refund_order_id': None,
                'error': str(e)
            }
    
    @staticmethod
    def split_payment(order, payments):
        """
        Process split payment with multiple methods
        
        payments: List[{
            'method': str,
            'amount': Decimal,
            'reference_id': str (optional)
        }]
        """
        try:
            total_received = sum(p['amount'] for p in payments)
            
            if total_received < order.total_amount:
                return {
                    'success': False,
                    'error': f'Total payment {total_received} < order total {order.total_amount}'
                }
            
            order.split_payment_details = payments
            order.payment_method = 'SPLIT'
            order.amount_paid = total_received
            order.change_amount = total_received - order.total_amount
            order.status = 'COMPLETED'
            order.completion_time = timezone.now()
            order.save()
            
            return {
                'success': True,
                'change': order.change_amount
            }
        
        except Exception as e:
            logger.error(f"Split payment error: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }


class OrderService:
    """Service for order management"""
    
    @staticmethod
    @transaction.atomic
    def create_order_with_items(restaurant, order_type, items_data, 
                                customer_name=None, customer_email=None, 
                                customer_phone=None, table=None, guest_count=1):
        """
        Create order with line items atomically
        
        items_data: List[{
            'menu_item_id': str,
            'quantity': int,
            'special_instructions': str (optional),
            'modifiers': List[{'name': str, 'price': Decimal}] (optional)
        }]
        """
        try:
            # Generate order number
            import uuid
            order_number = f"ORD-{timezone.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
            
            # Calculate subtotal
            subtotal = Decimal('0.00')
            order_items = []
            
            for item_data in items_data:
                try:
                    menu_item = MenuItem.objects.get(
                        id=item_data['menu_item_id'],
                        restaurant=restaurant
                    )
                except MenuItem.DoesNotExist:
                    raise ValidationError(f"Menu item not found: {item_data['menu_item_id']}")
                
                quantity = item_data.get('quantity', 1)
                item_total = menu_item.price * quantity
                subtotal += item_total
                
                order_items.append({
                    'menu_item': menu_item,
                    'quantity': quantity,
                    'unit_price': menu_item.price,
                    'total_price': item_total,
                    'special_instructions': item_data.get('special_instructions', ''),
                    'modifiers': item_data.get('modifiers', [])
                })
            
            # Calculate tax
            tax_rate = Decimal('0.08')  # 8% default tax
            tax_amount = subtotal * tax_rate
            total_amount = subtotal + tax_amount
            
            # Create order
            order = Order.objects.create(
                restaurant=restaurant,
                order_number=order_number,
                order_type=order_type,
                status='PENDING',
                subtotal=subtotal,
                tax_amount=tax_amount,
                total_amount=total_amount,
                customer_name=customer_name,
                customer_email=customer_email,
                customer_phone=customer_phone,
                table=table,
                guest_count=guest_count
            )
            
            # Create line items
            for item_data in order_items:
                line_item = OrderLineItem.objects.create(
                    order=order,
                    menu_item=item_data['menu_item'],
                    quantity=item_data['quantity'],
                    unit_price=item_data['unit_price'],
                    total_price=item_data['total_price'],
                    special_instructions=item_data['special_instructions']
                )
                
                # Add modifiers
                for modifier in item_data['modifiers']:
                    OrderModifier.objects.create(
                        order_line_item=line_item,
                        modifier_name=modifier['name'],
                        modifier_price=modifier.get('price', Decimal('0.00'))
                    )
            
            # Update table if dine-in
            if order_type == 'DINE_IN' and table:
                table.status = 'OCCUPIED'
                table.save()
            
            return order
        
        except Exception as e:
            logger.error(f"Order creation error: {str(e)}")
            raise
    
    @staticmethod
    def apply_discount_code(order, discount_code):
        """Apply discount code to order"""
        try:
            discount = Discount.objects.get(
                restaurant=order.restaurant,
                discount_code=discount_code
            )
            
            if not discount.is_valid():
                raise ValidationError("Discount code is not valid or has expired")
            
            discount_amount = discount.calculate_discount_amount(order.subtotal)
            
            # Update order
            order.discount_amount = discount_amount
            order.discount_reason = discount_code
            order.total_amount = order.subtotal + order.tax_amount - discount_amount
            
            # Increment usage
            discount.usage_count += 1
            discount.save()
            
            order.save()
            
            return {
                'success': True,
                'discount_amount': discount_amount,
                'new_total': order.total_amount
            }
        
        except Discount.DoesNotExist:
            raise ValidationError("Invalid discount code")
        except Exception as e:
            logger.error(f"Discount application error: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    @staticmethod
    def add_modifier_to_item(line_item, modifier_name, modifier_price):
        """Add modifier to order line item"""
        try:
            modifier = OrderModifier.objects.create(
                order_line_item=line_item,
                modifier_name=modifier_name,
                modifier_price=modifier_price
            )
            
            # Recalculate line item total
            line_item.total_price += modifier_price
            line_item.save()
            
            # Recalculate order totals
            order = line_item.order
            order.subtotal = sum(
                item.total_price 
                for item in order.line_items.all()
            )
            order.tax_amount = order.subtotal * Decimal('0.08')
            order.total_amount = order.subtotal + order.tax_amount - order.discount_amount
            order.save()
            
            return modifier
        
        except Exception as e:
            logger.error(f"Modifier addition error: {str(e)}")
            raise
    
    @staticmethod
    @transaction.atomic
    def cancel_order(order, cancellation_reason=''):
        """Cancel order and free up table"""
        try:
            if order.status in ['COMPLETED', 'CANCELLED']:
                raise ValidationError(f"Cannot cancel order with status {order.status}")
            
            order.status = 'CANCELLED'
            order.notes = f"{order.notes or ''}\nCancelled: {cancellation_reason}"
            order.save()
            
            # Free up table
            if order.table:
                order.table.status = 'AVAILABLE'
                order.table.save()
            
            logger.info(f"Order cancelled: {order.order_number}")
            
            return order
        
        except Exception as e:
            logger.error(f"Order cancellation error: {str(e)}")
            raise
    
    @staticmethod
    def get_order_summary(order):
        """Get comprehensive order summary with all details"""
        line_items = []
        
        for item in order.line_items.all():
            modifiers = OrderModifier.objects.filter(
                order_line_item=item
            ).values_list('modifier_name', 'modifier_price')
            
            line_items.append({
                'menu_item': item.menu_item.name,
                'quantity': item.quantity,
                'unit_price': float(item.unit_price),
                'total_price': float(item.total_price),
                'special_instructions': item.special_instructions,
                'modifiers': [{'name': m[0], 'price': float(m[1])} for m in modifiers]
            })
        
        return {
            'order_number': order.order_number,
            'status': order.status,
            'order_type': order.order_type,
            'customer_name': order.customer_name,
            'customer_email': order.customer_email,
            'customer_phone': order.customer_phone,
            'line_items': line_items,
            'subtotal': float(order.subtotal),
            'tax_amount': float(order.tax_amount),
            'discount_amount': float(order.discount_amount),
            'total_amount': float(order.total_amount),
            'payment_method': order.payment_method,
            'status_display': order.get_status_display(),
            'order_time': order.order_time.isoformat(),
            'completion_time': order.completion_time.isoformat() if order.completion_time else None,
        }


class POSAnalyticsService:
    """Service for POS analytics and reporting"""
    
    @staticmethod
    def get_daily_revenue(restaurant, date=None):
        """Get daily revenue summary"""
        if date is None:
            date = timezone.now().date()
        
        orders = Order.objects.filter(
            restaurant=restaurant,
            order_time__date=date,
            status='COMPLETED'
        )
        
        total_revenue = orders.aggregate(Sum('total_amount'))['total_amount__sum'] or Decimal('0.00')
        total_orders = orders.count()
        total_tips = orders.aggregate(Sum('tips'))['tips__sum'] or Decimal('0.00')
        
        # Payment method breakdown
        payment_breakdown = orders.values('payment_method').annotate(
            count=Count('id'),
            total=Sum('total_amount')
        )
        
        return {
            'date': date.isoformat(),
            'total_revenue': float(total_revenue),
            'total_orders': total_orders,
            'average_order_value': float(total_revenue / total_orders) if total_orders > 0 else 0,
            'total_tips': float(total_tips),
            'payment_methods': [
                {
                    'method': p['payment_method'],
                    'count': p['count'],
                    'total': float(p['total'])
                }
                for p in payment_breakdown
            ]
        }
    
    @staticmethod
    def get_top_items(restaurant, days=7):
        """Get top selling items"""
        end_date = timezone.now()
        start_date = end_date - timedelta(days=days)
        
        top_items = OrderLineItem.objects.filter(
            order__restaurant=restaurant,
            order__order_time__gte=start_date,
            order__order_time__lte=end_date,
            order__status='COMPLETED'
        ).values('menu_item__name', 'menu_item__id').annotate(
            total_quantity=Sum('quantity'),
            total_revenue=Sum('total_price'),
            order_count=Count('order', distinct=True)
        ).order_by('-total_revenue')[:10]
        
        return [
            {
                'item_id': str(item['menu_item__id']),
                'item_name': item['menu_item__name'],
                'quantity_sold': item['total_quantity'],
                'revenue': float(item['total_revenue']),
                'orders': item['order_count']
            }
            for item in top_items
        ]
    
    @staticmethod
    def get_payment_methods_report(restaurant, days=30):
        """Get payment methods breakdown"""
        end_date = timezone.now()
        start_date = end_date - timedelta(days=days)
        
        payment_data = Order.objects.filter(
            restaurant=restaurant,
            order_time__gte=start_date,
            order_time__lte=end_date,
            status='COMPLETED'
        ).values('payment_method').annotate(
            count=Count('id'),
            total_amount=Sum('total_amount'),
            avg_amount=Avg('total_amount')
        )
        
        return [
            {
                'payment_method': p['payment_method'] or 'Unknown',
                'transaction_count': p['count'],
                'total_amount': float(p['total_amount']),
                'average_transaction': float(p['avg_amount'])
            }
            for p in payment_data
        ]
    
    @staticmethod
    def get_peak_hours(restaurant, days=7):
        """Get peak ordering hours"""
        end_date = timezone.now()
        start_date = end_date - timedelta(days=days)
        
        peak_data = Order.objects.filter(
            restaurant=restaurant,
            order_time__gte=start_date,
            order_time__lte=end_date,
            status='COMPLETED'
        ).extra(
            select={'hour': 'EXTRACT(hour FROM order_time)'}
        ).values('hour').annotate(
            order_count=Count('id'),
            total_revenue=Sum('total_amount')
        ).order_by('hour')
        
        return [
            {
                'hour': int(p['hour']),
                'order_count': p['order_count'],
                'revenue': float(p['total_revenue'])
            }
            for p in peak_data
        ]
    
    @staticmethod
    def get_order_type_breakdown(restaurant, days=30):
        """Get breakdown by order type"""
        end_date = timezone.now()
        start_date = end_date - timedelta(days=days)
        
        breakdown = Order.objects.filter(
            restaurant=restaurant,
            order_time__gte=start_date,
            order_time__lte=end_date,
            status='COMPLETED'
        ).values('order_type').annotate(
            count=Count('id'),
            total_revenue=Sum('total_amount'),
            avg_order_value=Avg('total_amount')
        )
        
        return [
            {
                'order_type': b['order_type'],
                'count': b['count'],
                'revenue': float(b['total_revenue']),
                'average_value': float(b['avg_order_value'])
            }
            for b in breakdown
        ]