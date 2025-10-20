from django.db import models
from django.conf import settings
import uuid

class Staff(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='staff_profile')
    employee_id = models.CharField(max_length=20, unique=True, blank=True, null=True)
    date_joined = models.DateField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    department = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        verbose_name_plural = "Staff"
        db_table = 'staff_members'

    def __str__(self):
        return f'{self.user.first_name} {self.user.last_name} ({self.employee_id})'

class Category(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='product_categories')
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    display_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Categories"
        ordering = ['display_order', 'name']
        db_table = 'categories'

    def __str__(self):
        return f"{self.name} ({self.restaurant.name})"

class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='products')
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, related_name='products')
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    base_price = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)
    image = models.ImageField(upload_to='product_images/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Products"
        ordering = ['name']
        db_table = 'products'

    def __str__(self):
        return f"{self.name} ({self.restaurant.name})"

class Order(models.Model):
    ORDER_TYPES = (
        ('DINE_IN', 'Dine-In'),
        ('TAKEAWAY', 'Takeaway'),
        ('DELIVERY', 'Delivery'),
    )
    ORDER_STATUS = (
        ('PENDING', 'Pending'),
        ('PREPARING', 'Preparing'),
        ('READY', 'Ready'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='orders')
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='orders_taken')
    table_number = models.CharField(max_length=10, blank=True, null=True)
    order_type = models.CharField(max_length=10, choices=ORDER_TYPES, default='DINE_IN')
    status = models.CharField(max_length=10, choices=ORDER_STATUS, default='PENDING')
    customer_name = models.CharField(max_length=255, blank=True, null=True)
    customer_phone = models.CharField(max_length=20, blank=True, null=True)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Orders"
        ordering = ['-created_at']
        db_table = 'orders'

    def __str__(self):
        return f"Order #{self.id[:8]} for {self.restaurant.name}"

class OrderItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.IntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name_plural = "Order Items"
        db_table = 'order_items'

    def __str__(self):
        return f"{self.quantity} x {self.product.name} in Order #{self.order.id[:8]}"

class Table(models.Model):
    TABLE_STATUS_CHOICES = (
        ('AVAILABLE', 'Available'),
        ('OCCUPIED', 'Occupied'),
        ('NEEDS_CLEANING', 'Needs Cleaning'),
        ('OUT_OF_SERVICE', 'Out of Service'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='tables')
    number = models.CharField(max_length=10, unique=True)
    capacity = models.IntegerField(default=2)
    status = models.CharField(max_length=20, choices=TABLE_STATUS_CHOICES, default='AVAILABLE')
    current_order = models.OneToOneField(Order, on_delete=models.SET_NULL, null=True, blank=True, related_name='table')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tables'
        ordering = ['number']

    def __str__(self):
        return f"Table {self.number} ({self.restaurant.name}) - {self.status}"
