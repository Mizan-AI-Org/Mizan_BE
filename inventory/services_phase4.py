"""
Comprehensive Inventory Service Layer for Phase 4
- Stock management and alerts
- Demand forecasting
- Supplier optimization
- Inventory audits
- Cost analysis
"""
import logging
from decimal import Decimal
from datetime import timedelta, datetime
from django.utils import timezone
from django.db import transaction
from django.db.models import Sum, Count, Avg, Q
from django.core.exceptions import ValidationError

from .models import (
    InventoryItem, Supplier, PurchaseOrder, PurchaseOrderItem
)

logger = logging.getLogger(__name__)


class StockManagementService:
    """Service for stock level management and alerts"""
    
    @staticmethod
    @transaction.atomic
    def adjust_stock(inventory_item, quantity_change, movement_type='ADJUSTMENT', 
                    reference_id=None, notes=None, user=None):
        """
        Adjust stock level with audit trail
        
        quantity_change: positive (increase) or negative (decrease)
        """
        try:
            from .models_phase4 import StockLevel, StockMovement
            
            # Get or create stock level
            stock_level, _ = StockLevel.objects.get_or_create(
                inventory_item=inventory_item,
                defaults={'current_quantity': inventory_item.current_stock}
            )
            
            # Record movement
            old_quantity = stock_level.current_quantity
            new_quantity = old_quantity + quantity_change
            
            if new_quantity < 0:
                raise ValidationError(f"Insufficient stock. Available: {old_quantity}, Requested: {abs(quantity_change)}")
            
            # Update stock
            stock_level.current_quantity = new_quantity
            stock_level.available_quantity = new_quantity - stock_level.reserved_quantity
            
            if movement_type == 'PURCHASE':
                stock_level.last_restocked = timezone.now()
            
            stock_level.save()
            
            # Update inventory item
            inventory_item.current_stock = new_quantity
            inventory_item.last_restock_date = timezone.now() if movement_type == 'PURCHASE' else inventory_item.last_restock_date
            inventory_item.save()
            
            # Create movement record
            movement = StockMovement.objects.create(
                inventory_item=inventory_item,
                movement_type=movement_type,
                quantity_change=quantity_change,
                quantity_before=old_quantity,
                quantity_after=new_quantity,
                reference_id=reference_id,
                notes=notes,
                created_by=user
            )
            
            # Check for alerts
            StockManagementService._check_and_create_alerts(inventory_item, stock_level)
            
            return movement
        
        except Exception as e:
            logger.error(f"Stock adjustment error: {str(e)}")
            raise
    
    @staticmethod
    def reserve_stock(inventory_item, quantity, order_id=None):
        """Reserve stock for pending order"""
        try:
            from .models_phase4 import StockLevel
            
            stock_level, _ = StockLevel.objects.get_or_create(
                inventory_item=inventory_item,
                defaults={'current_quantity': inventory_item.current_stock}
            )
            
            if stock_level.available_quantity < quantity:
                raise ValidationError(
                    f"Insufficient stock to reserve. Available: {stock_level.available_quantity}, Requested: {quantity}"
                )
            
            stock_level.reserved_quantity += quantity
            stock_level.available_quantity -= quantity
            stock_level.save()
            
            logger.info(f"Stock reserved for order {order_id}: {quantity} {inventory_item.unit}")
            
            return True
        
        except Exception as e:
            logger.error(f"Stock reservation error: {str(e)}")
            raise
    
    @staticmethod
    def release_reserved_stock(inventory_item, quantity):
        """Release reserved stock (order cancelled, etc.)"""
        try:
            from .models_phase4 import StockLevel
            
            stock_level = StockLevel.objects.get(inventory_item=inventory_item)
            
            if stock_level.reserved_quantity < quantity:
                raise ValidationError("Invalid release quantity")
            
            stock_level.reserved_quantity -= quantity
            stock_level.available_quantity += quantity
            stock_level.save()
            
            return True
        
        except Exception as e:
            logger.error(f"Stock release error: {str(e)}")
            raise
    
    @staticmethod
    def consume_stock(inventory_item, quantity):
        """Consume reserved stock (order completed)"""
        try:
            from .models_phase4 import StockLevel, StockMovement
            
            stock_level = StockLevel.objects.get(inventory_item=inventory_item)
            
            if stock_level.reserved_quantity < quantity:
                raise ValidationError("Insufficient reserved stock")
            
            # Move from reserved to consumed
            stock_level.reserved_quantity -= quantity
            stock_level.current_quantity -= quantity
            stock_level.available_quantity = stock_level.current_quantity - stock_level.reserved_quantity
            stock_level.save()
            
            # Record usage
            StockMovement.objects.create(
                inventory_item=inventory_item,
                movement_type='USAGE',
                quantity_change=-quantity,
                quantity_before=stock_level.current_quantity + quantity,
                quantity_after=stock_level.current_quantity
            )
            
            # Update item
            inventory_item.current_stock = stock_level.current_quantity
            inventory_item.save()
            
            StockManagementService._check_and_create_alerts(inventory_item, stock_level)
            
            return True
        
        except Exception as e:
            logger.error(f"Stock consumption error: {str(e)}")
            raise
    
    @staticmethod
    def _check_and_create_alerts(inventory_item, stock_level=None):
        """Check stock levels and create alerts if needed"""
        try:
            from .models_phase4 import StockAlert
            
            if stock_level is None:
                from .models_phase4 import StockLevel
                stock_level, _ = StockLevel.objects.get_or_create(
                    inventory_item=inventory_item,
                    defaults={'current_quantity': inventory_item.current_stock}
                )
            
            current_stock = stock_level.current_quantity
            reorder_level = inventory_item.reorder_level or 0
            
            # Check for critical stock
            critical_level = reorder_level * Decimal('0.3')  # 30% of reorder level
            
            if current_stock <= critical_level:
                # Check if alert already exists
                existing = StockAlert.objects.filter(
                    inventory_item=inventory_item,
                    alert_type='CRITICAL',
                    status='ACTIVE'
                ).exists()
                
                if not existing:
                    StockAlert.objects.create(
                        inventory_item=inventory_item,
                        alert_type='CRITICAL',
                        current_stock=current_stock,
                        threshold_value=critical_level,
                        message=f"CRITICAL: {inventory_item.name} stock is at {current_stock} {inventory_item.unit}"
                    )
            
            # Check for low stock
            elif current_stock <= reorder_level:
                existing = StockAlert.objects.filter(
                    inventory_item=inventory_item,
                    alert_type='LOW_STOCK',
                    status='ACTIVE'
                ).exists()
                
                if not existing:
                    StockAlert.objects.create(
                        inventory_item=inventory_item,
                        alert_type='LOW_STOCK',
                        current_stock=current_stock,
                        threshold_value=reorder_level,
                        message=f"Low stock alert: {inventory_item.name} ({current_stock} {inventory_item.unit})"
                    )
        
        except Exception as e:
            logger.error(f"Alert creation error: {str(e)}")


class SupplierOptimizationService:
    """Service for supplier selection and order optimization"""
    
    @staticmethod
    def get_best_supplier(inventory_item, quantity):
        """Find best supplier based on price and lead time"""
        try:
            from .models_phase4 import SupplierPrice
            
            best_price = SupplierPrice.objects.filter(
                inventory_item=inventory_item,
                is_current=True,
                minimum_order__lte=quantity,
                effective_from__lte=timezone.now().date()
            ).select_related('supplier').order_by('unit_price').first()
            
            if not best_price:
                return None
            
            return {
                'supplier': best_price.supplier,
                'unit_price': best_price.unit_price,
                'lead_time': best_price.lead_time_days,
                'minimum_order': best_price.minimum_order,
                'total_cost': best_price.unit_price * quantity
            }
        
        except Exception as e:
            logger.error(f"Supplier optimization error: {str(e)}")
            return None
    
    @staticmethod
    @transaction.atomic
    def create_purchase_order(restaurant, items_data, supplier_id, expected_delivery_date=None, created_by=None):
        """
        Create purchase order with multiple items
        
        items_data: List[{
            'inventory_item_id': str,
            'quantity': Decimal,
            'unit_price': Decimal (optional)
        }]
        """
        try:
            supplier = Supplier.objects.get(id=supplier_id, restaurant=restaurant)
            
            # Calculate total
            total_amount = Decimal('0.00')
            order_items = []
            
            for item_data in items_data:
                try:
                    inv_item = InventoryItem.objects.get(
                        id=item_data['inventory_item_id'],
                        restaurant=restaurant
                    )
                except InventoryItem.DoesNotExist:
                    raise ValidationError(f"Inventory item not found: {item_data['inventory_item_id']}")
                
                quantity = item_data['quantity']
                unit_price = item_data.get('unit_price', inv_item.cost_per_unit)
                item_total = quantity * unit_price
                total_amount += item_total
                
                order_items.append({
                    'inventory_item': inv_item,
                    'quantity': quantity,
                    'unit_price': unit_price,
                    'item_total': item_total
                })
            
            # Create purchase order
            po = PurchaseOrder.objects.create(
                restaurant=restaurant,
                supplier=supplier,
                expected_delivery_date=expected_delivery_date,
                total_amount=total_amount,
                status='PENDING',
                created_by=created_by
            )
            
            # Create line items
            for item_data in order_items:
                PurchaseOrderItem.objects.create(
                    purchase_order=po,
                    inventory_item=item_data['inventory_item'],
                    quantity=item_data['quantity'],
                    unit_price=item_data['unit_price'],
                    line_total=item_data['item_total']
                )
            
            logger.info(f"Purchase order created: {po.id} for {supplier.name}")
            
            return po
        
        except Exception as e:
            logger.error(f"Purchase order creation error: {str(e)}")
            raise
    
    @staticmethod
    @transaction.atomic
    def receive_purchase_order(po, received_items=None):
        """
        Mark purchase order as received and update stock
        
        received_items: List[{
            'po_item_id': str,
            'received_quantity': Decimal
        }] (optional, defaults to full order)
        """
        try:
            if po.status == 'RECEIVED':
                raise ValidationError("Purchase order already received")
            
            po_items = po.purchase_order_items.all()
            
            if received_items:
                for received_item in received_items:
                    po_item = po_items.get(id=received_item['po_item_id'])
                    quantity = received_item.get('received_quantity', po_item.quantity)
                    
                    # Update stock
                    StockManagementService.adjust_stock(
                        po_item.inventory_item,
                        quantity,
                        movement_type='PURCHASE',
                        reference_id=str(po.id)
                    )
            else:
                # Receive all items
                for po_item in po_items:
                    StockManagementService.adjust_stock(
                        po_item.inventory_item,
                        po_item.quantity,
                        movement_type='PURCHASE',
                        reference_id=str(po.id)
                    )
            
            po.status = 'RECEIVED'
            po.delivery_date = timezone.now().date()
            po.save()
            
            return po
        
        except Exception as e:
            logger.error(f"PO receipt error: {str(e)}")
            raise


class DemandForecastingService:
    """Service for demand forecasting and inventory optimization"""
    
    @staticmethod
    def calculate_moving_average(inventory_item, days=7):
        """Calculate moving average of usage"""
        try:
            from .models_phase4 import StockMovement
            
            end_date = timezone.now()
            start_date = end_date - timedelta(days=days)
            
            usages = StockMovement.objects.filter(
                inventory_item=inventory_item,
                movement_type='USAGE',
                created_at__gte=start_date,
                created_at__lte=end_date
            ).aggregate(total_usage=Sum('quantity_change'))['total_usage'] or Decimal('0.00')
            
            average_daily = abs(usages) / days if usages != 0 else Decimal('0.00')
            return average_daily
        
        except Exception as e:
            logger.error(f"Moving average calculation error: {str(e)}")
            return Decimal('0.00')
    
    @staticmethod
    def forecast_demand(inventory_item, forecast_days=14):
        """Forecast demand for next N days"""
        try:
            daily_average = DemandForecastingService.calculate_moving_average(inventory_item, days=7)
            
            forecasts = []
            for i in range(forecast_days):
                forecast_date = timezone.now().date() + timedelta(days=i+1)
                forecasted_qty = daily_average  # Simple linear forecast
                
                forecasts.append({
                    'date': forecast_date.isoformat(),
                    'forecasted_quantity': float(forecasted_qty),
                    'confidence': 75  # Default confidence
                })
            
            return forecasts
        
        except Exception as e:
            logger.error(f"Forecast error: {str(e)}")
            return []
    
    @staticmethod
    def calculate_optimal_reorder_point(inventory_item, lead_time_days=3):
        """Calculate optimal reorder point based on usage and lead time"""
        try:
            daily_usage = DemandForecastingService.calculate_moving_average(inventory_item, days=30)
            
            # Reorder point = (daily usage × lead time) + safety stock
            safety_stock = daily_usage * Decimal('3')  # 3 days safety
            reorder_point = (daily_usage * lead_time_days) + safety_stock
            
            # Optimal order quantity (Economic Order Quantity approximation)
            if inventory_item.cost_per_unit > 0:
                monthly_usage = daily_usage * 30
                order_quantity = monthly_usage / 3  # Order monthly in 3 batches
            else:
                order_quantity = daily_usage * 7  # 1 week supply
            
            return {
                'reorder_point': float(reorder_point),
                'optimal_order_quantity': float(order_quantity),
                'daily_usage': float(daily_usage),
                'lead_time_days': lead_time_days
            }
        
        except Exception as e:
            logger.error(f"EOQ calculation error: {str(e)}")
            return None


class InventoryAuditService:
    """Service for conducting and managing physical inventory audits"""
    
    @staticmethod
    @transaction.atomic
    def start_audit(restaurant, started_by):
        """Start a new inventory audit"""
        try:
            from .models_phase4 import InventoryAudit
            
            audit = InventoryAudit.objects.create(
                restaurant=restaurant,
                audit_date=timezone.now().date(),
                started_by=started_by,
                status='IN_PROGRESS'
            )
            
            return audit
        
        except Exception as e:
            logger.error(f"Audit start error: {str(e)}")
            raise
    
    @staticmethod
    @transaction.atomic
    def record_audit_count(audit, inventory_item, counted_quantity, variance_reason=None):
        """Record count for single item in audit"""
        try:
            from .models_phase4 import InventoryAuditItem, StockMovement
            
            system_quantity = inventory_item.current_stock
            variance = counted_quantity - system_quantity
            
            audit_item = InventoryAuditItem.objects.create(
                audit=audit,
                inventory_item=inventory_item,
                system_quantity=system_quantity,
                counted_quantity=counted_quantity,
                variance=variance,
                variance_reason=variance_reason
            )
            
            # If variance exists, create adjustment
            if variance != 0:
                StockManagementService.adjust_stock(
                    inventory_item,
                    variance,
                    movement_type='COUNT',
                    reference_id=f"AUDIT-{audit.id}",
                    notes=f"Audit count adjustment: {variance_reason or 'No reason provided'}"
                )
                
                # Update audit total variance
                audit.total_variance += abs(variance * inventory_item.cost_per_unit)
                audit.save()
            
            return audit_item
        
        except Exception as e:
            logger.error(f"Audit count error: {str(e)}")
            raise
    
    @staticmethod
    @transaction.atomic
    def complete_audit(audit):
        """Complete audit and finalize counts"""
        try:
            audit.status = 'COMPLETED'
            audit.completed_at = timezone.now()
            audit.save()
            
            logger.info(f"Audit completed: {audit.id} with variance ${audit.total_variance}")
            
            return audit
        
        except Exception as e:
            logger.error(f"Audit completion error: {str(e)}")
            raise


class InventoryCostAnalysisService:
    """Service for cost analysis and COGS tracking"""
    
    @staticmethod
    def calculate_cogs(restaurant, start_date, end_date):
        """Calculate Cost of Goods Sold for period"""
        try:
            from .models_phase4 import StockMovement
            
            usage_cost = StockMovement.objects.filter(
                inventory_item__restaurant=restaurant,
                movement_type='USAGE',
                created_at__gte=start_date,
                created_at__lte=end_date
            ).aggregate(
                total_cost=Sum(
                    models.F('quantity_change') * models.F('inventory_item__cost_per_unit'),
                    output_field=models.DecimalField()
                )
            )['total_cost'] or Decimal('0.00')
            
            return abs(usage_cost)
        
        except Exception as e:
            logger.error(f"COGS calculation error: {str(e)}")
            return Decimal('0.00')
    
    @staticmethod
    def get_inventory_value(restaurant):
        """Calculate current inventory value"""
        items = InventoryItem.objects.filter(
            restaurant=restaurant,
            is_active=True
        ).aggregate(
            total_value=Sum(
                models.F('current_stock') * models.F('cost_per_unit'),
                output_field=models.DecimalField()
            )
        )['total_value'] or Decimal('0.00')
        
        return items
    
    @staticmethod
    def get_supplier_performance(restaurant, days=30):
        """Analyze supplier performance (delivery time, quality)"""
        try:
            end_date = timezone.now()
            start_date = end_date - timedelta(days=days)
            
            supplier_stats = PurchaseOrder.objects.filter(
                restaurant=restaurant,
                delivery_date__gte=start_date.date(),
                delivery_date__lte=end_date.date(),
                status='RECEIVED'
            ).values('supplier__name', 'supplier__id').annotate(
                total_orders=Count('id'),
                avg_days_to_delivery=Avg(
                    models.F('delivery_date') - models.F('order_date'),
                    output_field=models.DurationField()
                ),
                total_spent=Sum('total_amount')
            )
            
            return supplier_stats
        
        except Exception as e:
            logger.error(f"Supplier performance error: {str(e)}")
            return []