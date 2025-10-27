from django.db import models
from django.conf import settings
import uuid
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.utils import timezone

class Discount(models.Model):
    """Discount codes and promotions"""
    DISCOUNT_TYPE_CHOICES = (
        ('PERCENTAGE', 'Percentage'),
        ('FIXED_AMOUNT', 'Fixed Amount'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='discounts')
    discount_code = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True, null=True)
    discount_type = models.CharField(max_length=20, choices=DISCOUNT_TYPE_CHOICES)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)
    min_order_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    max_discount_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    max_usage = models.IntegerField(null=True, blank=True)
    usage_count = models.IntegerField(default=0)
    valid_from = models.DateTimeField(default=timezone.now)
    valid_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'pos_discounts'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.discount_code} - {self.discount_value}{'' if self.discount_type == 'FIXED_AMOUNT' else '%'}"
    
    def is_valid(self):
        """Check if discount is currently valid"""
        now = timezone.now()
        if not self.is_active:
            return False
        if self.valid_from > now:
            return False
        if self.valid_until and self.valid_until < now:
            return False
        if self.max_usage and self.usage_count >= self.max_usage:
            return False
        return True
    
    def calculate_discount_amount(self, order_subtotal):
        """Calculate discount amount for an order"""
        if not self.is_valid():
            raise ValidationError("This discount code is not valid")
        
        if order_subtotal < self.min_order_amount:
            raise ValidationError(f"Minimum order amount of {self.min_order_amount} required")
        
        if self.discount_type == 'PERCENTAGE':
            discount = order_subtotal * (self.discount_value / 100)
        else:
            discount = self.discount_value
        
        if self.max_discount_amount:
            discount = min(discount, self.max_discount_amount)
        
        return discount


class Table(models.Model):
    """Represents a physical dining table in the restaurant"""
    STATUS_CHOICES = (
        ('AVAILABLE', 'Available'),
        ('OCCUPIED', 'Occupied'),
        ('RESERVED', 'Reserved'),
        ('MAINTENANCE', 'Maintenance'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='tables')
    table_number = models.IntegerField()
    capacity = models.IntegerField(default=4)  # Number of seats
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='AVAILABLE')
    section = models.CharField(max_length=50, blank=True, null=True)  # e.g., 'Main', 'Patio', 'Bar'
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ['restaurant', 'table_number']
        ordering = ['table_number']
        db_table = 'pos_tables'
    
    def __str__(self):
        return f"Table {self.table_number} ({self.status})"


class Order(models.Model):
    """Represents a customer order in the POS system"""
    STATUS_CHOICES = (
        ('PENDING', 'Pending'),
        ('CONFIRMED', 'Confirmed'),
        ('PREPARING', 'Preparing'),
        ('READY', 'Ready'),
        ('SERVED', 'Served'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
    )
    
    ORDER_TYPE_CHOICES = (
        ('DINE_IN', 'Dine In'),
        ('TAKEOUT', 'Takeout'),
        ('DELIVERY', 'Delivery'),
        ('CATERING', 'Catering'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='pos_orders')
    table = models.ForeignKey(Table, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    server = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='pos_orders_served')
    
    order_number = models.CharField(max_length=50, unique=True)  # e.g., ORD-001
    order_type = models.CharField(max_length=20, choices=ORDER_TYPE_CHOICES, default='DINE_IN')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    
    # Amounts
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    discount_reason = models.CharField(max_length=255, blank=True, null=True)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    # Customer info (for takeout/delivery)
    customer_name = models.CharField(max_length=255, blank=True, null=True)
    customer_phone = models.CharField(max_length=20, blank=True, null=True)
    customer_email = models.EmailField(blank=True, null=True)
    
    # Delivery info
    delivery_address = models.TextField(blank=True, null=True)
    delivery_instructions = models.TextField(blank=True, null=True)
    
    # Timestamps
    order_time = models.DateTimeField(auto_now_add=True)
    ready_time = models.DateTimeField(null=True, blank=True)
    completion_time = models.DateTimeField(null=True, blank=True)
    
    # Metadata
    guest_count = models.IntegerField(default=1)  # For capacity planning
    notes = models.TextField(blank=True, null=True)
    is_priority = models.BooleanField(default=False)
    
    # Refund tracking
    REFUND_STATUS_CHOICES = (
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
        ('COMPLETED', 'Completed'),
    )
    refund_status = models.CharField(max_length=20, choices=REFUND_STATUS_CHOICES, null=True, blank=True)
    refund_reason = models.TextField(blank=True, null=True)
    refund_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    refund_date = models.DateTimeField(null=True, blank=True)
    source_order = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='refund_orders')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-order_time']
        db_table = 'pos_orders'
        indexes = [
            models.Index(fields=['restaurant', 'order_time']),
            models.Index(fields=['status']),
            models.Index(fields=['order_type']),
        ]
    
    def __str__(self):
        return f"{self.order_number} - {self.status}"
    
    def calculate_total(self):
        """Calculate total amount based on items, tax, and discount"""
        from django.db.models import Sum
        line_items_total = self.line_items.aggregate(Sum('total_price'))['total_price__sum'] or 0
        self.subtotal = line_items_total
        self.total_amount = self.subtotal + self.tax_amount - self.discount_amount
        self.save()


class OrderLineItem(models.Model):
    """Individual items in an order"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='line_items')
    menu_item = models.ForeignKey('menu.MenuItem', on_delete=models.PROTECT, related_name='order_items')
    
    quantity = models.IntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    
    # Special instructions
    special_instructions = models.TextField(blank=True, null=True)
    
    # Status tracking
    status = models.CharField(max_length=20, choices=[
        ('PENDING', 'Pending'),
        ('PREPARING', 'Preparing'),
        ('READY', 'Ready'),
        ('SERVED', 'Served'),
        ('CANCELLED', 'Cancelled'),
    ], default='PENDING')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'pos_order_line_items'
    
    def __str__(self):
        return f"{self.quantity}x {self.menu_item.name} - {self.order.order_number}"
    
    def save(self, *args, **kwargs):
        if not self.unit_price:
            self.unit_price = self.menu_item.price
        self.total_price = self.quantity * self.unit_price
        super().save(*args, **kwargs)


class OrderModifier(models.Model):
    """Additional modifiers/extras for order items (e.g., extra sauce, size upgrade)"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    line_item = models.ForeignKey(OrderLineItem, on_delete=models.CASCADE, related_name='modifiers')
    modifier_name = models.CharField(max_length=100)
    modifier_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'pos_order_modifiers'
    
    def __str__(self):
        return f"{self.modifier_name} - {self.modifier_price}"


class Payment(models.Model):
    """Payment transactions for orders"""
    PAYMENT_METHOD_CHOICES = (
        ('CASH', 'Cash'),
        ('CARD', 'Card'),
        ('DIGITAL_WALLET', 'Digital Wallet'),
        ('BANK_TRANSFER', 'Bank Transfer'),
        ('CHECK', 'Check'),
        ('CREDIT', 'Credit/Account'),
    )
    
    PAYMENT_STATUS_CHOICES = (
        ('PENDING', 'Pending'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
        ('REFUNDED', 'Refunded'),
        ('PARTIALLY_REFUNDED', 'Partially Refunded'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name='payment')
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='payments')
    
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='PENDING')
    
    # Transaction details
    transaction_id = models.CharField(max_length=100, unique=True, blank=True, null=True)
    processor_name = models.CharField(max_length=50, blank=True, null=True)  # e.g., 'Stripe', 'Square'
    
    # Partial payments
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    change_given = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    # Tip
    tip_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    # Refunds
    refund_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    refund_reason = models.CharField(max_length=255, blank=True, null=True)
    
    # Metadata
    processed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    payment_time = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-payment_time']
        db_table = 'pos_payments'
    
    def __str__(self):
        return f"Payment {self.id.hex[:8]} - {self.status}"


class POSTransaction(models.Model):
    """Audit log for POS transactions"""
    TRANSACTION_TYPE_CHOICES = (
        ('ORDER_CREATED', 'Order Created'),
        ('ORDER_UPDATED', 'Order Updated'),
        ('ORDER_CANCELLED', 'Order Cancelled'),
        ('PAYMENT_PROCESSED', 'Payment Processed'),
        ('PAYMENT_REFUNDED', 'Payment Refunded'),
        ('DISCOUNT_APPLIED', 'Discount Applied'),
        ('TABLE_ASSIGNED', 'Table Assigned'),
        ('ITEM_MODIFIED', 'Item Modified'),
        ('ITEM_REMOVED', 'Item Removed'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='pos_transactions')
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True, blank=True, related_name='transactions')
    
    transaction_type = models.CharField(max_length=50, choices=TRANSACTION_TYPE_CHOICES)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    
    # What changed
    description = models.TextField()
    previous_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    
    amount_involved = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        db_table = 'pos_transactions'
        indexes = [
            models.Index(fields=['restaurant', 'created_at']),
            models.Index(fields=['order']),
        ]
    
    def __str__(self):
        return f"{self.transaction_type} - {self.created_at}"


class ReceiptSetting(models.Model):
    """Receipt printing and formatting settings per restaurant"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.OneToOneField('accounts.Restaurant', on_delete=models.CASCADE, related_name='receipt_settings')
    
    # Header & Footer
    header_text = models.TextField(blank=True, null=True)
    footer_text = models.TextField(blank=True, null=True)
    
    # Display options
    show_item_codes = models.BooleanField(default=False)
    show_item_descriptions = models.BooleanField(default=True)
    show_unit_prices = models.BooleanField(default=False)
    show_discount_details = models.BooleanField(default=True)
    show_tax_breakdown = models.BooleanField(default=True)
    
    # Printer settings
    paper_width = models.IntegerField(default=80)  # in mm
    font_size_items = models.IntegerField(default=12)
    font_size_total = models.IntegerField(default=14)
    
    # Logo
    logo = models.ImageField(upload_to='receipt_logos/', blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'pos_receipt_settings'
    
    def __str__(self):
        return f"Receipt Settings - {self.restaurant.name}"