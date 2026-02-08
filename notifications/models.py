from django.db import models
import uuid
from accounts.models import CustomUser
from django.utils import timezone

class Notification(models.Model):
    NOTIFICATION_TYPES = (
        ('SHIFT_ASSIGNED', 'Shift Assigned'),
        ('SHIFT_UPDATED', 'Shift Updated'),
        ('SHIFT_CANCELLED', 'Shift Cancelled'),
        ('SHIFT_REMINDER', 'Shift Reminder'),
        ('TASK_ASSIGNED', 'Task Assigned'),
        ('TASK_COMPLETED', 'Task Completed'),
        ('TASK_OVERDUE', 'Task Overdue'),
        ('AVAILABILITY_REQUEST', 'Availability Request'),
        ('AVAILABILITY_APPROVED', 'Availability Approved'),
        ('AVAILABILITY_DENIED', 'Availability Denied'),
        ('STAFF_REQUEST', 'Staff Request'),
        ('COMPLIANCE_ALERT', 'Compliance Alert'),
        ('SAFETY_BRIEFING', 'Safety Briefing'),
        ('ANNOUNCEMENT', 'Announcement'),
        ('BREAK_REQUEST', 'Break Request'),
        ('EMERGENCY', 'Emergency'),
        ('SYSTEM_ALERT', 'System Alert'),
        ('INVITATION', 'Staff Invitation'),
        ('OTHER', 'Other'),
    )
    
    PRIORITY_LEVELS = (
        ('LOW', 'Low'),
        ('MEDIUM', 'Medium'),
        ('HIGH', 'High'),
        ('URGENT', 'Urgent'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='notifications')
    sender = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='sent_notifications')
    
    # Content
    title = models.CharField(max_length=255, blank=True, null=True)
    message = models.TextField()
    notification_type = models.CharField(max_length=30, choices=NOTIFICATION_TYPES, default='OTHER')
    priority = models.CharField(max_length=10, choices=PRIORITY_LEVELS, default='MEDIUM')
    
    # Metadata
    data = models.JSONField(default=dict, blank=True, help_text="Additional structured data")
    
    # Status tracking
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    # Delivery tracking
    channels_sent = models.JSONField(default=list, help_text="Channels through which notification was sent")
    delivery_status = models.JSONField(default=dict, help_text="Delivery status for each channel")
    
    # Related objects
    related_shift_id = models.UUIDField(null=True, blank=True, help_text="Related shift ID")
    related_task_id = models.UUIDField(null=True, blank=True, help_text="Related task ID")
    
    # Expiration
    expires_at = models.DateTimeField(null=True, blank=True, help_text="When notification expires")
    
    def mark_as_read(self):
        """Mark notification as read"""
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=['is_read', 'read_at'])
    
    def is_expired(self):
        """Check if notification has expired"""
        if self.expires_at:
            return timezone.now() > self.expires_at
        return False
    
    class Meta:
        db_table = 'notifications'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient', 'is_read']),
            models.Index(fields=['recipient', 'notification_type']),
            models.Index(fields=['created_at']),
            models.Index(fields=['priority']),
        ]

    def __str__(self):
        return f"Notification for {self.recipient.email} - {self.notification_type}"

class DeviceToken(models.Model):
    DEVICE_TYPES = (
        ('ANDROID', 'Android'),
        ('IOS', 'iOS'),
        ('WEB', 'Web Browser'),
    )
    
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='device_tokens')
    token = models.CharField(max_length=255, unique=True)
    device_type = models.CharField(max_length=10, choices=DEVICE_TYPES, default='WEB')
    device_name = models.CharField(max_length=100, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'device_tokens'
        verbose_name = "Device Token"
        verbose_name_plural = "Device Tokens"
        indexes = [
            models.Index(fields=['user', 'is_active']),
            models.Index(fields=['token']),
        ]

    def __str__(self):
        return f"Token for {self.user.email}: {self.token[:30]}..."


class NotificationPreference(models.Model):
    """User notification preferences for different channels and types"""
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='notification_preferences')
    
    # Channel preferences
    email_enabled = models.BooleanField(default=True)
    push_enabled = models.BooleanField(default=True)
    whatsapp_enabled = models.BooleanField(default=True)
    sms_enabled = models.BooleanField(default=False)
    
    # Notification type preferences
    shift_notifications = models.BooleanField(default=True)
    task_notifications = models.BooleanField(default=True)
    availability_notifications = models.BooleanField(default=True)
    compliance_notifications = models.BooleanField(default=True)
    emergency_notifications = models.BooleanField(default=True)
    announcement_notifications = models.BooleanField(default=True)
    
    # Timing preferences
    quiet_hours_enabled = models.BooleanField(default=False)
    quiet_hours_start = models.TimeField(null=True, blank=True, help_text="Start of quiet hours")
    quiet_hours_end = models.TimeField(null=True, blank=True, help_text="End of quiet hours")
    
    # Frequency preferences
    digest_enabled = models.BooleanField(default=False, help_text="Receive daily digest instead of individual notifications")
    digest_time = models.TimeField(null=True, blank=True, help_text="Time to send daily digest")
    
    # WhatsApp specific
    whatsapp_number = models.CharField(max_length=20, blank=True, null=True, help_text="WhatsApp number for notifications")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'notification_preferences'
    
    def __str__(self):
        return f"Notification preferences for {self.user.email}"


class NotificationTemplate(models.Model):
    """Templates for different types of notifications"""
    TEMPLATE_TYPES = (
        ('SHIFT_ASSIGNED', 'Shift Assigned'),
        ('SHIFT_UPDATED', 'Shift Updated'),
        ('SHIFT_CANCELLED', 'Shift Cancelled'),
        ('SHIFT_REMINDER', 'Shift Reminder'),
        ('TASK_ASSIGNED', 'Task Assigned'),
        ('AVAILABILITY_REQUEST', 'Availability Request'),
        ('COMPLIANCE_ALERT', 'Compliance Alert'),
        ('EMERGENCY', 'Emergency'),
    )
    
    CHANNEL_TYPES = (
        ('EMAIL', 'Email'),
        ('WHATSAPP', 'WhatsApp'),
        ('PUSH', 'Push Notification'),
        ('SMS', 'SMS'),
    )
    
    name = models.CharField(max_length=100)
    template_type = models.CharField(max_length=30, choices=TEMPLATE_TYPES)
    channel = models.CharField(max_length=20, choices=CHANNEL_TYPES)
    
    # Template content
    subject_template = models.CharField(max_length=255, blank=True, null=True, help_text="Subject/title template")
    body_template = models.TextField(help_text="Message body template with placeholders")
    
    # Template variables documentation
    available_variables = models.JSONField(default=list, help_text="List of available template variables")
    
    # Status
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True)
    
    class Meta:
        db_table = 'notification_templates'
        unique_together = ('template_type', 'channel', 'is_default')
        indexes = [
            models.Index(fields=['template_type', 'channel']),
            models.Index(fields=['is_active']),
        ]
    
    def __str__(self):
        return f"{self.name} - {self.get_template_type_display()} ({self.get_channel_display()})"


class NotificationLog(models.Model):
    """Log of all notification attempts for audit and debugging"""
    STATUS_CHOICES = (
        ('PENDING', 'Pending'),
        ('SENT', 'Sent'),
        ('DELIVERED', 'Delivered'),
        ('READ', 'Read'),
        ('FAILED', 'Failed'),
        ('BOUNCED', 'Bounced'),
    )
    
    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, related_name='delivery_logs')
    channel = models.CharField(max_length=20)
    recipient_address = models.CharField(max_length=255, help_text="Email, phone number, or device token")
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    attempt_count = models.IntegerField(default=1)
    
    # Response data
    external_id = models.CharField(max_length=255, blank=True, null=True, help_text="External service message ID")
    response_data = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, null=True)
    
    # Timestamps
    sent_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'notification_logs'
        ordering = ['-sent_at']
        indexes = [
            models.Index(fields=['notification', 'channel']),
            models.Index(fields=['status']),
            models.Index(fields=['sent_at']),
        ]
    
    def __str__(self):
        return f"{self.channel} notification to {self.recipient_address} - {self.status}"


class NotificationAttachment(models.Model):
    """File attachments associated with a notification (e.g., announcement documents)"""
    notification = models.ForeignKey(
        Notification,
        on_delete=models.CASCADE,
        related_name='attachments'
    )
    file = models.FileField(upload_to='notification_attachments/')
    original_name = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=100, blank=True)
    file_size = models.PositiveIntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'notification_attachments'
        ordering = ['-uploaded_at']
        indexes = [
            models.Index(fields=['notification']),
        ]

    def __str__(self):
        return self.original_name or (self.file.name if self.file else 'Attachment')


class NotificationIssue(models.Model):
    """Reports from staff about undelivered or problematic announcements"""
    STATUS_CHOICES = (
        ('OPEN', 'Open'),
        ('INVESTIGATING', 'Investigating'),
        ('RESOLVED', 'Resolved'),
    )

    reporter = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='notification_issues')
    notification = models.ForeignKey(Notification, on_delete=models.SET_NULL, null=True, blank=True, related_name='issues')
    description = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='OPEN')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'notification_issues'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"Issue by {self.reporter.email} - {self.status}"


class WhatsAppMessageProcessed(models.Model):
    """Idempotency table: skip processing duplicate WhatsApp messages."""
    wamid = models.CharField(max_length=255, unique=True, db_index=True)
    processed_at = models.DateTimeField(default=timezone.now)
    channel = models.CharField(max_length=20, default='whatsapp')

    class Meta:
        db_table = 'whatsapp_message_processed'
        indexes = [models.Index(fields=['wamid'])]
        verbose_name = 'WhatsApp Message Processed'
        verbose_name_plural = 'WhatsApp Messages Processed'


class WhatsAppSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='whatsapp_sessions')
    phone = models.CharField(max_length=20, db_index=True, unique=True)
    state = models.CharField(max_length=50, default='idle')
    context = models.JSONField(default=dict, blank=True)
    last_interaction_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'whatsapp_sessions'
        indexes = [
            models.Index(fields=['phone']),
            models.Index(fields=['state']),
        ]
