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


class Incident(models.Model):
    """
    Formal record of an incident reported by staff or automated systems.
    """
    PRIORITY_CHOICES = (
        ('LOW', 'Low'),
        ('MEDIUM', 'Medium'),
        ('HIGH', 'High'),
        ('CRITICAL', 'Critical'),
    )
    
    STATUS_CHOICES = (
        ('OPEN', 'Open'),
        ('INVESTIGATING', 'Investigating'),
        ('RESOLVED', 'Resolved'),
        ('CLOSED', 'Closed'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='incidents')
    reporter = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='reported_incidents')
    
    title = models.CharField(max_length=255)
    description = models.TextField()
    
    # Classification
    category = models.CharField(max_length=100, blank=True, null=True) # e.g., 'Maintenance', 'Safety', 'HR'
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='MEDIUM')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='OPEN')
    
    # Evidence
    photo_evidence = models.JSONField(default=list, blank=True)
    audio_evidence = models.JSONField(default=list, blank=True) # URLs to audio files
    
    # Resolution
    assigned_to = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_incidents')
    resolution_notes = models.TextField(blank=True, null=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'incidents'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['restaurant', 'status']),
            models.Index(fields=['priority']),
        ]
    
    def __str__(self):
        return f"{self.title} ({self.status}) - {self.restaurant.name}"