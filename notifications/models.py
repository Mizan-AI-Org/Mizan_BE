from django.db import models
import uuid
from accounts.models import CustomUser

class Notification(models.Model):
    NOTIFICATION_TYPES = (
        ('SHIFT_UPDATE', 'Shift Update'),
        ('ANNOUNCEMENT', 'Announcement'),
        ('BREAK_REQUEST', 'Break Request'),
        ('EMERGENCY', 'Emergency'),
        ('OTHER', 'Other'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='notifications')
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)
    notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES, default='OTHER')
    
    class Meta:
        db_table = 'notifications'
        ordering = ['-created_at']

    def __str__(self):
        return f"Notification for {self.recipient.email} - {self.notification_type}"

class DeviceToken(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='device_tokens')
    token = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'device_tokens'
        verbose_name = "Device Token"
        verbose_name_plural = "Device Tokens"

    def __str__(self):
        return f"Token for {self.user.email}: {self.token[:30]}..."
