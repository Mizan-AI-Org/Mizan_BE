from django.db import models
from django.conf import settings
import uuid

class Report(models.Model):
    REPORT_TYPES = (
        ('SALES_SUMMARY', 'Sales Summary'),
        ('ATTENDANCE_OVERVIEW', 'Attendance Overview'),
        ('INVENTORY_STATUS', 'Inventory Status'),
        ('SHIFT_PERFORMANCE', 'Shift Performance'),
        ('OTHER', 'Other'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='reports')
    report_type = models.CharField(max_length=50, choices=REPORT_TYPES)
    generated_at = models.DateTimeField(auto_now_add=True)
    data = models.JSONField() # Stores the report data in JSON format
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='generated_reports')

    class Meta:
        ordering = ('-generated_at',)
        db_table = 'reports'

    def __str__(self):
        return f'{self.report_type} Report for {self.restaurant.name} on {self.generated_at.date()}'