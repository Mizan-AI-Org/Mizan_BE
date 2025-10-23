from django.db import models
import uuid
from accounts.models import Restaurant, CustomUser

class DailySalesReport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='daily_sales_reports')
    date = models.DateField(unique=True)
    total_revenue = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    total_orders = models.IntegerField(default=0)
    avg_order_value = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    top_selling_items = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['date']
        unique_together = ('restaurant', 'date')

    def __str__(self):
        return f"Daily Sales Report for {self.restaurant.name} on {self.date}"

class AttendanceReport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='attendance_reports')
    date = models.DateField(unique=True)
    total_staff_hours = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    staff_on_shift = models.IntegerField(default=0)
    late_arrivals = models.IntegerField(default=0)
    absences = models.IntegerField(default=0)
    attendance_details = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['date']
        unique_together = ('restaurant', 'date')

    def __str__(self):
        return f"Attendance Report for {self.restaurant.name} on {self.date}"

class InventoryReport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='inventory_reports')
    date = models.DateField(unique=True)
    total_inventory_value = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    low_stock_items = models.JSONField(default=list)
    waste_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    stock_adjustment_summary = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['date']
        unique_together = ('restaurant', 'date')

    def __str__(self):
        return f"Inventory Report for {self.restaurant.name} on {self.date}"