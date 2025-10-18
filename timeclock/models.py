from django.db import models
import uuid

class ClockEvent(models.Model):
    EVENT_TYPES = [
        ('in', 'Clock In'),
        ('out', 'Clock Out'),
        ('break_start', 'Break Start'),
        ('break_end', 'Break End'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    staff = models.ForeignKey('accounts.CustomUser', on_delete=models.CASCADE, related_name='clock_events')
    event_type = models.CharField(max_length=20, choices=EVENT_TYPES)
    timestamp = models.DateTimeField(auto_now_add=True)
    photo = models.ImageField(upload_to='clock_photos/', null=True, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    device_id = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    
    class Meta:
        ordering = ['-timestamp']
    
    def __str__(self):
        return f"{self.staff.username} - {self.event_type} - {self.timestamp}"

class Shift(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    staff = models.ForeignKey('accounts.CustomUser', on_delete=models.CASCADE, related_name='shifts')
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    scheduled_hours = models.DecimalField(max_digits=4, decimal_places=2, default=0)
    actual_hours = models.DecimalField(max_digits=4, decimal_places=2, default=0)
    section = models.CharField(max_length=100, blank=True)  # For table assignments
    status = models.CharField(max_length=20, default='scheduled', 
                             choices=[('scheduled', 'Scheduled'), ('completed', 'Completed'), ('cancelled', 'Cancelled')])
    
    def save(self, *args, **kwargs):
        # Calculate scheduled hours
        if self.start_time and self.end_time:
            hours = (self.end_time - self.start_time).total_seconds() / 3600
            self.scheduled_hours = round(hours, 2)
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.staff.username} - {self.start_time.date()}"