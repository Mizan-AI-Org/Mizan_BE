"""
Enhanced Inventory Models for Phase 4 - Advanced Stock Management
- Stock tracking and alerts
- Forecasting and predictions
- Supplier management
- Recipe/Menu item integration
"""
from django.db import models
from django.utils import timezone
import uuid
from decimal import Decimal


class StockLevel(models.Model):
    """Real-time stock level tracking with history"""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inventory_item = models.OneToOneField('inventory.InventoryItem', on_delete=models.CASCADE, related_name='stock_level')
    current_quantity = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    reserved_quantity = models.DecimalField(max_digits=10, decimal_places=2, default=0)  # For pending orders
    available_quantity = models.DecimalField(max_digits=10, decimal_places=2, default=0)  # Current - Reserved
    last_counted = models.DateTimeField(null=True, blank=True)
    last_restocked = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'inventory_stock_levels'
    
    def __str__(self):
        return f"{self.inventory_item.name} - Available: {self.available_quantity}"


class StockMovement(models.Model):
    """History of all stock movements for audit trail"""
    
    MOVEMENT_TYPE_CHOICES = (
        ('PURCHASE', 'Purchase/Restock'),
        ('USAGE', 'Usage/Consumption'),
        ('ADJUSTMENT', 'Manual Adjustment'),
        ('RETURN', 'Return to Supplier'),
        ('DAMAGE', 'Damaged/Waste'),
        ('TRANSFER', 'Transfer between locations'),
        ('COUNT', 'Inventory Count'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inventory_item = models.ForeignKey('inventory.InventoryItem', on_delete=models.CASCADE, related_name='stock_movements')
    movement_type = models.CharField(max_length=20, choices=MOVEMENT_TYPE_CHOICES)
    quantity_change = models.DecimalField(max_digits=10, decimal_places=2)  # Positive or negative
    quantity_before = models.DecimalField(max_digits=10, decimal_places=2)
    quantity_after = models.DecimalField(max_digits=10, decimal_places=2)
    reference_id = models.CharField(max_length=100, blank=True, null=True)  # PO ID, Order ID, etc.
    notes = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey('accounts.CustomUser', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'inventory_stock_movements'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['inventory_item', 'created_at']),
            models.Index(fields=['movement_type']),
        ]
    
    def __str__(self):
        return f"{self.get_movement_type_display()} - {self.quantity_change} {self.inventory_item.unit}"


class StockAlert(models.Model):
    """Alerts for low stock, overstock, or unusual patterns"""
    
    ALERT_TYPE_CHOICES = (
        ('LOW_STOCK', 'Low Stock Alert'),
        ('CRITICAL', 'Critical Stock Level'),
        ('OVERSTOCK', 'Overstock Alert'),
        ('EXPIRY_WARNING', 'Expiry Warning'),
        ('UNUSUAL_USAGE', 'Unusual Usage Pattern'),
    )
    
    ALERT_STATUS_CHOICES = (
        ('ACTIVE', 'Active'),
        ('ACKNOWLEDGED', 'Acknowledged'),
        ('RESOLVED', 'Resolved'),
        ('DISMISSED', 'Dismissed'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inventory_item = models.ForeignKey('inventory.InventoryItem', on_delete=models.CASCADE, related_name='alerts')
    alert_type = models.CharField(max_length=20, choices=ALERT_TYPE_CHOICES)
    status = models.CharField(max_length=20, choices=ALERT_STATUS_CHOICES, default='ACTIVE')
    current_stock = models.DecimalField(max_digits=10, decimal_places=2)
    threshold_value = models.DecimalField(max_digits=10, decimal_places=2)
    message = models.TextField()
    acknowledged_by = models.ForeignKey('accounts.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='acknowledged_alerts')
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'inventory_stock_alerts'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.get_alert_type_display()} - {self.inventory_item.name}"


class Forecast(models.Model):
    """Demand forecasting and predictive analytics"""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inventory_item = models.ForeignKey('inventory.InventoryItem', on_delete=models.CASCADE, related_name='forecasts')
    forecast_date = models.DateField()
    forecasted_quantity = models.DecimalField(max_digits=10, decimal_places=2)
    confidence_level = models.IntegerField(help_text="Confidence percentage (0-100)")
    forecast_method = models.CharField(max_length=50)  # e.g., 'linear_regression', 'moving_average'
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'inventory_forecasts'
        unique_together = ['inventory_item', 'forecast_date']
        ordering = ['forecast_date']
    
    def __str__(self):
        return f"Forecast for {self.inventory_item.name} on {self.forecast_date}"


class RecipeIngredient(models.Model):
    """Ingredients required for menu items"""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    menu_item = models.ForeignKey('menu.MenuItem', on_delete=models.CASCADE, related_name='ingredients')
    inventory_item = models.ForeignKey('inventory.InventoryItem', on_delete=models.CASCADE, related_name='used_in_recipes')
    quantity_required = models.DecimalField(max_digits=10, decimal_places=3)  # Amount per dish
    unit = models.CharField(max_length=10)  # Should match inventory item unit
    cost_per_portion = models.DecimalField(max_digits=10, decimal_places=2)  # Calculated cost
    is_optional = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'inventory_recipe_ingredients'
        unique_together = ['menu_item', 'inventory_item']
    
    def __str__(self):
        return f"{self.inventory_item.name} ({self.quantity_required} {self.unit}) for {self.menu_item.name}"


class SupplierContact(models.Model):
    """Additional supplier contact information"""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    supplier = models.ForeignKey('inventory.Supplier', on_delete=models.CASCADE, related_name='contacts')
    contact_type = models.CharField(
        max_length=20,
        choices=[('SALES', 'Sales'), ('SUPPORT', 'Support'), ('BILLING', 'Billing')],
        default='SALES'
    )
    contact_name = models.CharField(max_length=255)
    email = models.EmailField()
    phone = models.CharField(max_length=20)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'inventory_supplier_contacts'
    
    def __str__(self):
        return f"{self.contact_name} ({self.supplier.name})"


class SupplierPrice(models.Model):
    """Historical pricing from suppliers for cost tracking"""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    supplier = models.ForeignKey('inventory.Supplier', on_delete=models.CASCADE, related_name='prices')
    inventory_item = models.ForeignKey('inventory.InventoryItem', on_delete=models.CASCADE, related_name='supplier_prices')
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    minimum_order = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    lead_time_days = models.IntegerField(help_text="Days to delivery")
    is_current = models.BooleanField(default=True)
    effective_from = models.DateField()
    effective_until = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'inventory_supplier_prices'
        ordering = ['-effective_from']
    
    def __str__(self):
        return f"{self.supplier.name} - {self.inventory_item.name}: ${self.unit_price}"


class InventoryAudit(models.Model):
    """Physical inventory audits and counts"""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='inventory_audits')
    audit_date = models.DateField()
    started_by = models.ForeignKey('accounts.CustomUser', on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=[('STARTED', 'Started'), ('IN_PROGRESS', 'In Progress'), ('COMPLETED', 'Completed')],
        default='STARTED'
    )
    total_variance = models.DecimalField(max_digits=12, decimal_places=2, default=0)  # $ value of discrepancies
    notes = models.TextField(blank=True, null=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'inventory_audits'
        ordering = ['-audit_date']
    
    def __str__(self):
        return f"Audit - {self.restaurant.name} ({self.audit_date})"


class InventoryAuditItem(models.Model):
    """Individual items in an audit"""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    audit = models.ForeignKey(InventoryAudit, on_delete=models.CASCADE, related_name='audit_items')
    inventory_item = models.ForeignKey('inventory.InventoryItem', on_delete=models.CASCADE)
    system_quantity = models.DecimalField(max_digits=10, decimal_places=2)
    counted_quantity = models.DecimalField(max_digits=10, decimal_places=2)
    variance = models.DecimalField(max_digits=10, decimal_places=2)  # Counted - System
    variance_reason = models.CharField(
        max_length=100,
        choices=[('DAMAGE', 'Damage'), ('USAGE', 'Usage'), ('COUNT_ERROR', 'Counting Error'), ('UNKNOWN', 'Unknown')],
        blank=True, null=True
    )
    
    class Meta:
        db_table = 'inventory_audit_items'
    
    def __str__(self):
        return f"{self.inventory_item.name} - Variance: {self.variance}"