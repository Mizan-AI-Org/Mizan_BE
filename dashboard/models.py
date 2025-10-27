from django.db import models
import uuid
from django.conf import settings

class DailyKPI(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='daily_kpis')
    date = models.DateField(unique_for_date=['restaurant'])
    total_revenue = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    total_orders = models.IntegerField(default=0)
    avg_order_value = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    food_waste_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    labor_cost_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    inventory_value = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    revenue_lost_to_stockouts = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    staff_online_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Daily KPIs"
        ordering = ['date']
        db_table = 'daily_kpis'

    def __str__(self):
        return f"KPI for {self.restaurant.name} on {self.date}"

class Alert(models.Model):
    ALERT_TYPES = (
        ('INFO', 'Information'),
        ('WARNING', 'Warning'),
        ('ERROR', 'Error'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='alerts')
    message = models.TextField()
    alert_type = models.CharField(max_length=10, choices=ALERT_TYPES, default='INFO')
    is_resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Alerts"
        ordering = ['-created_at']
        db_table = 'alerts'

    def __str__(self):
        return f"{self.alert_type} for {self.restaurant.name}: {self.message[:50]}"

class Task(models.Model):
    TASK_PRIORITY = (
        ('LOW', 'Low'),
        ('MEDIUM', 'Medium'),
        ('HIGH', 'High'),
    )

    TASK_STATUS = (
        ('PENDING', 'Pending'),
        ('IN_PROGRESS', 'In Progress'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='tasks')
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='dashboard_assigned_tasks')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    priority = models.CharField(max_length=10, choices=TASK_PRIORITY, default='MEDIUM')
    status = models.CharField(max_length=20, choices=TASK_STATUS, default='PENDING')
    due_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Tasks"
        ordering = ['due_date', 'priority']
        db_table = 'tasks'

    def __str__(self):
        return f"Task: {self.title} ({self.status}) for {self.restaurant.name}"
