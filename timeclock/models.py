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
    # Align model with existing DB column to prevent NOT NULL insert failures
    location_encrypted = models.TextField(db_column='location_encrypted')
    
    class Meta:
        ordering = ['-timestamp']
    
    def __str__(self):
        return f"{self.staff.username} - {self.event_type} - {self.timestamp}"