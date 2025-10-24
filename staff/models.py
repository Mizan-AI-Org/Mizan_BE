from django.db import models
from accounts.models import CustomUser

class Schedule(models.Model):
    staff = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='schedules')
    title = models.CharField(max_length=255, default='Shift')
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    tasks = models.JSONField(default=list)  # Store tasks as a JSON array of strings
    is_recurring = models.BooleanField(default=False)
    recurrence_pattern = models.CharField(max_length=50, blank=True, null=True) # e.g., 'daily', 'weekly', 'monthly', 'custom'

    def __str__(self):
        return f"{self.staff.username}'s schedule for {self.start_time.strftime('%Y-%m-%d %H:%M')}"

    class Meta:
        ordering = ['start_time']
