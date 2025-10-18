from django.db import models
import uuid

class Report(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE)
    report_type = models.CharField(max_length=50, choices=[
        ('attendance', 'Attendance Report'),
        ('payroll', 'Payroll Report'),
        ('performance', 'Performance Report'),
        ('inventory', 'Inventory Report'),
    ])
    date_from = models.DateField()
    date_to = models.DateField()
    generated_at = models.DateTimeField(auto_now_add=True)
    data = models.JSONField()  # Store report data as JSON
    
    def __str__(self):
        return f"{self.report_type} - {self.date_from} to {self.date_to}"