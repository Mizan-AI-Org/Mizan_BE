from django.db import models
from django.utils import timezone
import uuid
from accounts.models import CustomUser, Restaurant
from django.core.exceptions import ValidationError
from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
import logging

logger = logging.getLogger(__name__)

class StaffProfile(models.Model):
    """Extended profile information for staff members"""
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='staff_profile')
    profile_image = models.ImageField(upload_to='staff_profiles/', null=True, blank=True)
    emergency_contact_name = models.CharField(max_length=255, blank=True, null=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True, null=True)
    date_of_birth = models.DateField(null=True, blank=True)
    hire_date = models.DateField(default=timezone.now)
    position = models.CharField(max_length=100, blank=True, null=True)
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    salary_type = models.CharField(max_length=10, choices=[('HOURLY', 'Hourly'), ('MONTHLY', 'Monthly')], default='HOURLY')
    department = models.CharField(max_length=100, blank=True, null=True)
    promotion_history = models.JSONField(default=list, blank=True)
    skills = models.JSONField(default=list, blank=True)
    certifications = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True, null=True)
    
    def __str__(self):
        return f"{self.user.username}'s Profile"

class StaffDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    staff = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='documents')
    title = models.CharField(max_length=255)
    file = models.FileField(upload_to='staff_documents/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} - {self.staff.username}"

class Schedule(models.Model):
    """Enhanced schedule model with reliability and safety features"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    staff = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='schedules')
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='schedules', null=True)
    title = models.CharField(max_length=255, default='Shift')
    description = models.TextField(blank=True, null=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    # Safety-focused fields
    break_duration = models.IntegerField(default=30, help_text="Break duration in minutes")
    position_requirements = models.JSONField(null=True, blank=True, help_text="Required skills/certifications for this position")
    tasks = models.JSONField(default=list)  # Store tasks as a JSON array of strings
    is_recurring = models.BooleanField(default=False)
    recurrence_pattern = models.CharField(max_length=50, blank=True, null=True) # e.g., 'daily', 'weekly', 'monthly', 'custom'
    recurrence_end_date = models.DateField(null=True, blank=True)
    color = models.CharField(max_length=20, default='#3498db')  # Color for UI display
    status = models.CharField(max_length=20, default='SCHEDULED', choices=[
        ('SCHEDULED', 'Scheduled'),
        ('CONFIRMED', 'Confirmed'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
    ])
    # Safety features
    safety_briefing_required = models.BooleanField(default=False)
    safety_briefing_completed = models.BooleanField(default=False)
    safety_briefing_completed_at = models.DateTimeField(null=True, blank=True)
    safety_briefing_completed_by = models.ForeignKey(
        'accounts.CustomUser', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='completed_briefings'
    )
    ppe_requirements = models.JSONField(null=True, blank=True, help_text="Required PPE for this shift")
    safety_compliance_status = models.CharField(
        max_length=20,
        choices=[
            ('COMPLIANT', 'Compliant'),
            ('NON_COMPLIANT', 'Non-Compliant'),
            ('NEEDS_REVIEW', 'Needs Review'),
            ('NOT_APPLICABLE', 'Not Applicable')
        ],
        default='NOT_APPLICABLE'
    )
    safety_compliance_notes = models.TextField(blank=True)
    
    # Bidding/preference system
    is_open_for_bidding = models.BooleanField(default=False)
    bidding_deadline = models.DateTimeField(null=True, blank=True)
    preferred_staff = models.JSONField(null=True, blank=True, help_text="List of staff IDs who have bid for this shift")
    
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, related_name='created_schedules')
    last_modified_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, related_name='modified_schedules')
    
    # Backup of schedule data for recovery purposes
    backup_data = models.JSONField(default=dict, blank=True)
    
    def clean(self):
        """Validate schedule data"""
        if self.start_time >= self.end_time:
            raise ValidationError("End time must be after start time")
        
        if self.is_recurring and not self.recurrence_pattern:
            raise ValidationError("Recurrence pattern is required for recurring schedules")
    
    def save(self, *args, **kwargs):
        """Override save to create backup data"""
        # Create backup of current data before saving
        if self.pk:
            self.backup_data = {
                'title': self.title,
                'start_time': self.start_time.isoformat(),
                'end_time': self.end_time.isoformat(),
                'tasks': self.tasks,
                'is_recurring': self.is_recurring,
                'recurrence_pattern': self.recurrence_pattern,
                'status': self.status,
                'updated_at': timezone.now().isoformat()
            }
        
        # Call the original save method
        super().save(*args, **kwargs)
        
        # Log the save operation
        logger.info(f"Schedule {self.id} saved successfully for staff {self.staff.id}")

    def __str__(self):
        return f"{self.staff.username}'s schedule for {self.start_time.strftime('%Y-%m-%d %H:%M')}"

    class Meta:
        ordering = ['start_time']
        indexes = [
            models.Index(fields=['staff', 'start_time']),
            models.Index(fields=['restaurant', 'start_time']),
            models.Index(fields=['status']),
        ]

class ScheduleChange(models.Model):
    """Audit trail for schedule changes"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    schedule = models.ForeignKey(Schedule, on_delete=models.CASCADE, related_name='changes')
    changed_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    previous_data = models.JSONField()
    new_data = models.JSONField()
    change_type = models.CharField(max_length=20, choices=[
        ('CREATE', 'Created'),
        ('UPDATE', 'Updated'),
        ('DELETE', 'Deleted'),
    ])
    
    class Meta:
        ordering = ['-timestamp']

class ScheduleNotification(models.Model):
    """Notifications for schedule changes"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    schedule = models.ForeignKey(Schedule, on_delete=models.CASCADE, related_name='notifications')
    recipient = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='schedule_notifications')
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']

class StaffAvailability(models.Model):
    """Staff availability preferences and time-off requests"""
    AVAILABILITY_TYPE_CHOICES = [
        ('REGULAR', 'Regular Availability'),
        ('TIME_OFF', 'Time Off Request'),
        ('PREFERRED', 'Preferred Shift'),
        ('UNAVAILABLE', 'Unavailable'),
        ('EMERGENCY', 'Emergency Unavailability'),
    ]
    
    REQUEST_STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('DENIED', 'Denied'),
        ('CANCELLED', 'Cancelled'),
    ]
    
    PRIORITY_CHOICES = [
        ('LOW', 'Low'),
        ('MEDIUM', 'Medium'),
        ('HIGH', 'High'),
        ('URGENT', 'Urgent'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    staff = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='availability')
    
    # Availability type and status
    availability_type = models.CharField(max_length=20, choices=AVAILABILITY_TYPE_CHOICES, default='REGULAR')
    status = models.CharField(max_length=20, choices=REQUEST_STATUS_CHOICES, default='APPROVED')
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='MEDIUM')
    
    # Time specifications
    day_of_week = models.IntegerField(
        choices=[(i, day) for i, day in enumerate(['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'])],
        null=True, blank=True,
        help_text="For recurring availability patterns"
    )
    specific_date = models.DateField(null=True, blank=True, help_text="For specific date requests")
    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)
    start_datetime = models.DateTimeField(null=True, blank=True, help_text="For specific datetime requests")
    end_datetime = models.DateTimeField(null=True, blank=True, help_text="For specific datetime requests")
    
    # Availability preferences
    is_available = models.BooleanField(default=True)
    preferred_hours_per_week = models.IntegerField(null=True, blank=True, help_text="Preferred weekly hours")
    max_consecutive_days = models.IntegerField(null=True, blank=True, help_text="Maximum consecutive working days")
    min_hours_between_shifts = models.IntegerField(default=8, help_text="Minimum hours between shifts")
    
    # Request details
    reason = models.TextField(blank=True, null=True, help_text="Reason for time-off or availability change")
    notes = models.TextField(blank=True, null=True, help_text="Additional notes or special requirements")
    
    # Approval workflow
    requested_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    reviewed_by = models.ForeignKey(
        CustomUser, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='reviewed_availability_requests'
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    approval_notes = models.TextField(blank=True, null=True)
    
    # Recurring patterns
    is_recurring = models.BooleanField(default=False)
    recurrence_pattern = models.CharField(
        max_length=20,
        choices=[
            ('DAILY', 'Daily'),
            ('WEEKLY', 'Weekly'),
            ('BIWEEKLY', 'Bi-weekly'),
            ('MONTHLY', 'Monthly'),
        ],
        null=True, blank=True
    )
    recurrence_end_date = models.DateField(null=True, blank=True)
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def clean(self):
        """Validate availability data"""
        from django.core.exceptions import ValidationError
        
        # Ensure either day_of_week or specific_date is provided
        if not self.day_of_week and not self.specific_date:
            raise ValidationError("Either day_of_week or specific_date must be specified")
        
        # Validate time ranges
        if self.start_time and self.end_time:
            if self.start_time >= self.end_time:
                raise ValidationError("Start time must be before end time")
        
        if self.start_datetime and self.end_datetime:
            if self.start_datetime >= self.end_datetime:
                raise ValidationError("Start datetime must be before end datetime")
        
        # Validate recurring patterns
        if self.is_recurring and not self.recurrence_pattern:
            raise ValidationError("Recurrence pattern is required for recurring availability")
    
    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)
    
    def __str__(self):
        if self.availability_type == 'TIME_OFF':
            date_str = self.specific_date.strftime('%Y-%m-%d') if self.specific_date else f"{self.get_day_of_week_display()}"
            return f"{self.staff.get_full_name()} - Time Off: {date_str}"
        elif self.day_of_week is not None:
            return f"{self.staff.get_full_name()} - {self.get_day_of_week_display()}: {self.start_time}-{self.end_time}"
        else:
            return f"{self.staff.get_full_name()} - {self.specific_date}: {self.start_time}-{self.end_time}"
    
    class Meta:
        ordering = ['day_of_week', 'specific_date', 'start_time']
        indexes = [
            models.Index(fields=['staff', 'availability_type']),
            models.Index(fields=['staff', 'status']),
            models.Index(fields=['specific_date']),
            models.Index(fields=['day_of_week']),
        ]

class PerformanceMetric(models.Model):
    """Staff performance tracking"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    staff = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='performance_metrics')
    metric_type = models.CharField(max_length=50)
    value = models.FloatField()
    date = models.DateField()
    notes = models.TextField(blank=True, null=True)
    
    class Meta:
        ordering = ['-date']


class StaffRequest(models.Model):
    """
    Manager-facing staff requests inbox item.
    Created from WhatsApp/Lua agent ingestion (and can later be created from in-app staff UI).
    """
    STATUS_CHOICES = (
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
        ('ESCALATED', 'Escalated'),
        ('CLOSED', 'Closed'),
    )

    PRIORITY_CHOICES = (
        ('LOW', 'Low'),
        ('MEDIUM', 'Medium'),
        ('HIGH', 'High'),
        ('URGENT', 'Urgent'),
    )

    CATEGORY_CHOICES = (
        ('DOCUMENT', 'Document'),
        ('HR', 'HR'),
        ('SCHEDULING', 'Scheduling'),
        ('PAYROLL', 'Payroll'),
        ('OPERATIONS', 'Operations'),
        ('OTHER', 'Other'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='staff_requests')

    # Best-effort staff linkage (phone-only requests are common on WhatsApp)
    staff = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='staff_requests')
    staff_name = models.CharField(max_length=255, blank=True, default='')
    staff_phone = models.CharField(max_length=32, blank=True, default='')

    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='OTHER')
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='MEDIUM')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')

    subject = models.CharField(max_length=255, blank=True, default='')
    description = models.TextField(blank=True, default='')

    source = models.CharField(max_length=30, blank=True, default='whatsapp', help_text="Origin channel: whatsapp/lua/web")
    external_id = models.CharField(max_length=128, blank=True, default='', help_text="External inquiry/ticket id if applicable")
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    reviewed_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviewed_staff_requests')
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['restaurant', 'status']),
            models.Index(fields=['restaurant', 'created_at']),
            models.Index(fields=['staff_phone']),
        ]

    def __str__(self):
        return f"StaffRequest {str(self.id)[:8]} - {self.status}"


class StaffRequestComment(models.Model):
    """
    Request timeline item: comments + status changes.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(StaffRequest, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='staff_request_comments')

    kind = models.CharField(max_length=20, default='comment', help_text="comment|status_change|system")
    body = models.TextField(blank=True, default='')
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['request', 'created_at']),
        ]

    def __str__(self):
        return f"StaffRequestComment {str(self.id)[:8]}"

@receiver(pre_save, sender=Schedule)
def log_schedule_changes(sender, instance, **kwargs):
    """Log changes to schedules before saving"""
    if instance.pk:
        try:
            old_instance = Schedule.objects.get(pk=instance.pk)
            # Create change record
            change_data = {
                'previous': {
                    'title': old_instance.title,
                    'start_time': old_instance.start_time.isoformat(),
                    'end_time': old_instance.end_time.isoformat(),
                    'status': old_instance.status,
                },
                'new': {
                    'title': instance.title,
                    'start_time': instance.start_time.isoformat(),
                    'end_time': instance.end_time.isoformat(),
                    'status': instance.status,
                }
            }
            
            # Store the change record after save
            instance._change_data = change_data
            
        except Schedule.DoesNotExist:
            # This is a new instance
            pass

@receiver(post_save, sender=Schedule)
def create_schedule_change_record(sender, instance, created, **kwargs):
    """Create audit record after schedule is saved"""
    if hasattr(instance, '_change_data'):
        # Create change record
        ScheduleChange.objects.create(
            schedule=instance,
            changed_by=instance.last_modified_by,
            previous_data=instance._change_data['previous'],
            new_data=instance._change_data['new'],
            change_type='UPDATE'
        )
    elif created:
        # New schedule created
        ScheduleChange.objects.create(
            schedule=instance,
            changed_by=instance.created_by,
            previous_data={},
            new_data={
                'title': instance.title,
                'start_time': instance.start_time.isoformat(),
                'end_time': instance.end_time.isoformat(),
                'status': instance.status,
            },
            change_type='CREATE'
        )
        
        # Create notification for the staff member
        ScheduleNotification.objects.create(
            schedule=instance,
            recipient=instance.staff,
            message=f"You have been scheduled for {instance.title} on {instance.start_time.strftime('%Y-%m-%d %H:%M')}"
        )
