from django.db import models
import uuid
from django.conf import settings

class InventoryItem(models.Model):
    UNIT_CHOICES = (
        ('KG', 'Kilograms'),
        ('GRAM', 'Grams'),
        ('LITER', 'Liters'),
        ('ML', 'Milliliters'),
        ('UNIT', 'Units'),
        ('BOX', 'Box'),
        ('BAG', 'Bag'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='inventory_items')
    name = models.CharField(max_length=255)
    current_stock = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    unit = models.CharField(max_length=10, choices=UNIT_CHOICES)
    reorder_level = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    cost_per_unit = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    supplier = models.ForeignKey('Supplier', on_delete=models.SET_NULL, null=True, blank=True, related_name='supplied_items')
    last_restock_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['restaurant', 'name']
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.current_stock} {self.unit})"

class Supplier(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='suppliers')
    name = models.CharField(max_length=255)
    contact_person = models.CharField(max_length=255, blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['restaurant', 'name']
        ordering = ['name']

    def __str__(self):
        return self.name

class PurchaseOrder(models.Model):
    STATUS_CHOICES = (
        ('PENDING', 'Pending'),
        ('ORDERED', 'Ordered'),
        ('RECEIVED', 'Received'),
        ('CANCELLED', 'Cancelled'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='purchase_orders')
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='purchase_orders')
    order_date = models.DateField(auto_now_add=True)
    expected_delivery_date = models.DateField(null=True, blank=True)
    delivery_date = models.DateField(null=True, blank=True)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-order_date']

    def __str__(self):
        return f"PO#{self.id.hex[:8]} - {self.supplier.name} ({self.order_date})"

class PurchaseOrderItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='items')
    inventory_item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    total_price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        unique_together = ['purchase_order', 'inventory_item']

    def __str__(self):
        return f"{self.quantity} x {self.inventory_item.name} for PO#{self.purchase_order.id.hex[:8]}"

class WasteEntry(models.Model):
    """Individual waste report — staff report via WhatsApp to Miya.
    Tracks what was wasted, quantity, reason, and cost."""
    REASON_CHOICES = [
        ('EXPIRED', 'Expired'),
        ('SPOILED', 'Spoiled'),
        ('OVERPRODUCTION', 'Overproduction'),
        ('DROPPED', 'Dropped / Damaged'),
        ('RETURNED', 'Customer Return'),
        ('QUALITY', 'Quality Issue'),
        ('OTHER', 'Other'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='waste_entries')
    inventory_item = models.ForeignKey(InventoryItem, on_delete=models.SET_NULL, null=True, blank=True, related_name='waste_entries')
    item_name = models.CharField(max_length=255, help_text="Free-text name (used when item not in inventory system)")
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit = models.CharField(max_length=20, blank=True)
    estimated_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Auto-calculated from inventory cost_per_unit or manual")
    reason = models.CharField(max_length=20, choices=REASON_CHOICES, default='OTHER')
    notes = models.TextField(blank=True)
    reported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='waste_reports')
    photo = models.ImageField(upload_to='waste_photos/', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    waste_date = models.DateField(help_text="Date the waste occurred")

    class Meta:
        db_table = 'waste_entries'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['restaurant', 'waste_date']),
        ]

    def __str__(self):
        return f"Waste: {self.quantity} {self.unit} {self.item_name} ({self.waste_date})"


class InventoryCountSession(models.Model):
    """A conversational inventory count session — Miya walks staff through items one by one."""
    STATUS_CHOICES = [
        ('IN_PROGRESS', 'In Progress'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='inventory_count_sessions')
    counted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='IN_PROGRESS')
    items_total = models.IntegerField(default=0)
    items_counted = models.IntegerField(default=0)
    current_item_index = models.IntegerField(default=0, help_text="Index into the ordered item list")
    count_data = models.JSONField(default=dict, help_text="Map of item_id -> {counted: Decimal, expected: Decimal, variance: Decimal}")
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    count_date = models.DateField()

    class Meta:
        db_table = 'inventory_count_sessions'
        ordering = ['-count_date']

    def __str__(self):
        return f"Count {self.count_date} by {self.counted_by} ({self.status})"


class StockAdjustment(models.Model):
    ADJUSTMENT_TYPES = (
        ('ADD', 'Add Stock'),
        ('REMOVE', 'Remove Stock'),
        ('WASTE', 'Waste'),
        ('TRANSFER', 'Transfer'), # Future use for multi-location
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='stock_adjustments')
    inventory_item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name='adjustments')
    adjustment_type = models.CharField(max_length=20, choices=ADJUSTMENT_TYPES)
    quantity_changed = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.TextField(blank=True, null=True)
    adjusted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.adjustment_type} {self.quantity_changed} {self.inventory_item.unit} of {self.inventory_item.name}"
