from django.db import models
import uuid
from django.utils import timezone
from django.conf import settings
from django.core.exceptions import ValidationError

# Import audit models to make them discoverable by Django migrations
from .audit import AuditLog, AuditTrailService, AuditMixin

class ScheduleTemplate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'schedule_templates'
        ordering = ['name']
        indexes = [
            models.Index(fields=['restaurant', 'is_active']),
        ]
    
    def __str__(self):
        return f"{self.name} - {self.restaurant.name}"

class TemplateShift(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template = models.ForeignKey(ScheduleTemplate, on_delete=models.CASCADE, related_name='shifts')
    role = models.CharField(max_length=20, choices=settings.STAFF_ROLES_CHOICES)
    day_of_week = models.IntegerField(choices=[(0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'),
                                              (3, 'Thursday'), (4, 'Friday'), (5, 'Saturday'), (6, 'Sunday')])
    start_time = models.TimeField()
    end_time = models.TimeField()
    required_staff = models.IntegerField(default=1)
    
    class Meta:
        unique_together = ['template', 'role', 'day_of_week']
    
    def __str__(self):
        return f"{self.get_day_of_week_display()} - {self.role}"

class WeeklySchedule(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE)
    week_start = models.DateField()
    week_end = models.DateField()
    is_published = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['restaurant', 'week_start']
    
    def __str__(self):
        return f"Week of {self.week_start} - {self.restaurant.name}"

class AssignedShift(models.Model):
    STATUS_CHOICES = (
        ('SCHEDULED', 'Scheduled'),
        ('CONFIRMED', 'Confirmed'),
        ('IN_PROGRESS', 'In Progress'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
        ('NO_SHOW', 'No Show'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    schedule = models.ForeignKey(WeeklySchedule, on_delete=models.CASCADE, related_name='assigned_shifts')
    staff = models.ForeignKey('accounts.CustomUser', on_delete=models.CASCADE, related_name='assigned_shifts')
    shift_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    break_duration = models.DurationField(default=timezone.timedelta(minutes=30))
    role = models.CharField(max_length=20, choices=settings.STAFF_ROLES_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='SCHEDULED')
    notes = models.TextField(blank=True, null=True)
    is_confirmed = models.BooleanField(default=False)
    color = models.CharField(max_length=7, default='#6b7280', blank=True, null=True)
    
    # Enhanced fields for comprehensive shift management
    required_skills = models.JSONField(default=list, help_text="List of required skills for this shift")
    required_certifications = models.JSONField(default=list, help_text="List of required certifications")
    equipment_needed = models.JSONField(default=list, help_text="List of required equipment/tools")
    preparation_instructions = models.TextField(blank=True, null=True, help_text="Special preparation instructions")
    
    # Location and workspace details
    workspace_location = models.CharField(max_length=255, blank=True, null=True, help_text="Specific workspace/station assignment")
    department = models.CharField(max_length=100, blank=True, null=True, help_text="Department assignment")
    
    # Compliance and safety
    safety_briefing_required = models.BooleanField(default=False)
    safety_briefing_completed = models.BooleanField(default=False)
    safety_briefing_completed_at = models.DateTimeField(null=True, blank=True)
    compliance_checks_required = models.JSONField(default=list, help_text="List of required compliance checks")
    
    # Notification tracking
    notification_sent = models.BooleanField(default=False)
    notification_sent_at = models.DateTimeField(null=True, blank=True)
    notification_channels = models.JSONField(default=list, help_text="Channels used for notification (whatsapp, email, app)")
    
    # Timezone support
    timezone = models.CharField(max_length=50, default='UTC', help_text="Timezone for shift times")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey('accounts.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='created_shifts')
    last_modified_by = models.ForeignKey('accounts.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='modified_shifts')

    class Meta:
        db_table = 'assigned_shifts'
        unique_together = ['schedule', 'staff', 'shift_date']
        ordering = ['shift_date', 'start_time']
        indexes = [
            models.Index(fields=['staff', 'shift_date']),
            models.Index(fields=['status', 'shift_date']),
        ]
    
    def __str__(self):
        return f'{self.staff.first_name} {self.staff.last_name} - {self.shift_date} ({self.start_time}-{self.end_time})'

    def clean(self):
        """Validate shift doesn't conflict with other shifts"""
        from django.db.models import Q
        
        # Check for overlapping shifts
        overlapping = AssignedShift.objects.filter(
            staff=self.staff,
            shift_date=self.shift_date,
            status__in=['SCHEDULED', 'CONFIRMED', 'COMPLETED']
        ).exclude(id=self.id)
        
        # Convert to datetime for comparison
        shift_start = timezone.datetime.combine(self.shift_date, self.start_time)
        shift_end = timezone.datetime.combine(self.shift_date, self.end_time)
        
        for existing_shift in overlapping:
            existing_start = timezone.datetime.combine(existing_shift.shift_date, existing_shift.start_time)
            existing_end = timezone.datetime.combine(existing_shift.shift_date, existing_shift.end_time)
            
            if shift_start < existing_end and shift_end > existing_start:
                raise ValidationError(f"Staff member has overlapping shift from {existing_shift.start_time} to {existing_shift.end_time}")
    
    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    @property
    def actual_hours(self):
        """Calculate actual working hours excluding break time"""
        shift_start_datetime = timezone.datetime.combine(self.shift_date, self.start_time)
        shift_end_datetime = timezone.datetime.combine(self.shift_date, self.end_time)
        
        # Handle overnight shifts
        if shift_end_datetime < shift_start_datetime:
            shift_end_datetime += timezone.timedelta(days=1)
        
        duration = shift_end_datetime - shift_start_datetime
        
        # Subtract break duration if it exists
        if self.break_duration:
            duration -= self.break_duration
            
        return duration.total_seconds() / 3600
    
    @property
    def is_today(self):
        """Check if shift is today"""
        return self.shift_date == timezone.now().date()
    
    @property
    def is_upcoming(self):
        """Check if shift is in the future"""
        return self.shift_date >= timezone.now().date()

class ShiftSwapRequest(models.Model):
    STATUS_CHOICES = (
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
        ('CANCELLED', 'Cancelled'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shift_to_swap = models.ForeignKey(AssignedShift, on_delete=models.CASCADE, related_name='swap_requests_out')
    requester = models.ForeignKey('accounts.CustomUser', on_delete=models.CASCADE, related_name='initiated_swap_requests')
    # The staff member who is requested to take the shift, can be null for open requests
    receiver = models.ForeignKey('accounts.CustomUser', on_delete=models.SET_NULL, related_name='received_swap_requests', null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    request_message = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'shift_swap_requests'
        ordering = ['-created_at']

    def __str__(self):
        return f"Shift Swap Request from {self.requester.first_name} for {self.shift_to_swap}"


class TaskCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='task_categories')
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    color = models.CharField(max_length=7, default='#3B82F6')  # Hex color code
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'task_categories'
        unique_together = ['restaurant', 'name']
        verbose_name_plural = 'Task Categories'
    
    def __str__(self):
        return f"{self.name} - {self.restaurant.name}"


class ShiftTask(models.Model):
    PRIORITY_CHOICES = (
        ('LOW', 'Low'),
        ('MEDIUM', 'Medium'),
        ('HIGH', 'High'),
        ('URGENT', 'Urgent'),
    )
    
    STATUS_CHOICES = (
        ('TODO', 'To Do'),
        ('IN_PROGRESS', 'In Progress'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
    )
    
    VERIFICATION_TYPES = (
        ('NONE', 'No Verification Required'),
        ('PHOTO', 'Photo Evidence Required'),
        ('DOCUMENT', 'Document Upload Required'),
        ('SIGNATURE', 'Digital Signature Required'),
        ('CHECKLIST', 'Checklist Completion Required'),
        ('SUPERVISOR_APPROVAL', 'Supervisor Approval Required'),
        ('TEMPERATURE_LOG', 'Temperature Recording Required'),
        ('QUANTITY_COUNT', 'Quantity/Count Recording Required'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shift = models.ForeignKey(AssignedShift, on_delete=models.CASCADE, related_name='tasks')
    category = models.ForeignKey(TaskCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='shift_tasks')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='MEDIUM')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='TODO')
    assigned_to = models.ForeignKey('accounts.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='shift_assigned_tasks')
    estimated_duration = models.DurationField(null=True, blank=True)  # Time estimate
    parent_task = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='subtasks')
    notes = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey('accounts.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='shift_created_tasks')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    # Enhanced SOP and Compliance Fields
    sop_document = models.TextField(blank=True, null=True, help_text="Standard Operating Procedure instructions")
    sop_steps = models.JSONField(default=list, help_text="Step-by-step SOP instructions as JSON array")
    sop_version = models.CharField(max_length=20, blank=True, null=True, help_text="SOP version number")
    
    # Compliance and Safety Requirements
    compliance_checks = models.JSONField(default=list, help_text="List of required compliance checks")
    safety_requirements = models.JSONField(default=list, help_text="Safety requirements and protocols")
    quality_standards = models.JSONField(default=list, help_text="Quality control standards to meet")
    
    # Verification Requirements
    verification_type = models.CharField(max_length=30, choices=VERIFICATION_TYPES, default='NONE')
    verification_required = models.BooleanField(default=False)
    verification_instructions = models.TextField(blank=True, null=True, help_text="Instructions for verification process")
    verification_checklist = models.JSONField(default=list, help_text="Checklist items for verification")
    
    # Equipment and Tools
    required_equipment = models.JSONField(default=list, help_text="List of required equipment/tools")
    required_materials = models.JSONField(default=list, help_text="List of required materials/supplies")
    
    # Skills and Certifications
    required_skills = models.JSONField(default=list, help_text="Skills required to complete this task")
    required_certifications = models.JSONField(default=list, help_text="Certifications required for this task")
    
    # Maintenance and Routine Information
    maintenance_type = models.CharField(max_length=100, blank=True, null=True, help_text="Type of maintenance task")
    maintenance_frequency = models.CharField(max_length=50, blank=True, null=True, help_text="How often this maintenance should occur")
    last_maintenance_date = models.DateTimeField(null=True, blank=True, help_text="When this maintenance was last performed")
    
    # Critical Task Indicators
    is_critical = models.BooleanField(default=False, help_text="Mark as critical task requiring immediate attention")
    supervisor_notification_required = models.BooleanField(default=False, help_text="Notify supervisor when task status changes")
    
    # Completion Tracking
    completion_percentage = models.IntegerField(default=0, help_text="Task completion percentage (0-100)")
    actual_duration = models.DurationField(null=True, blank=True, help_text="Actual time taken to complete task")
    started_at = models.DateTimeField(null=True, blank=True, help_text="When task was started")
    
    # Dependencies
    depends_on_tasks = models.ManyToManyField('self', symmetrical=False, blank=True, related_name='dependent_tasks', help_text="Tasks that must be completed before this one")
    
    # Location and Context
    location_specific = models.CharField(max_length=255, blank=True, null=True, help_text="Specific location where task should be performed")
    environmental_conditions = models.JSONField(default=dict, help_text="Required environmental conditions (temperature, humidity, etc.)")
    
    # Recurring Task Information
    is_recurring = models.BooleanField(default=False)
    recurrence_pattern = models.CharField(max_length=50, blank=True, null=True, help_text="Pattern for recurring tasks (daily, weekly, etc.)")
    next_occurrence = models.DateTimeField(null=True, blank=True, help_text="When this task should next occur")
    
    class Meta:
        db_table = 'shift_tasks'
        ordering = ['-priority', 'created_at']
        indexes = [
            models.Index(fields=['shift', 'status']),
            models.Index(fields=['assigned_to', 'status']),
        ]
    
    def __str__(self):
        return f"{self.title} - {self.shift}"
    
    def mark_completed(self):
        self.status = 'COMPLETED'
        self.completed_at = timezone.now()
        self.save()
    
    def get_progress_percentage(self):
        """Calculate progress based on subtasks completion"""
        if not self.subtasks.exists():
            return 0 if self.status == 'TODO' else (50 if self.status == 'IN_PROGRESS' else 100)
        
        completed = self.subtasks.filter(status='COMPLETED').count()
        total = self.subtasks.count()
        return int((completed / total) * 100) if total > 0 else 0


class TaskVerificationRecord(models.Model):
    """Model to track task verification and completion evidence"""
    VERIFICATION_STATUS_CHOICES = (
        ('PENDING', 'Pending Verification'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
        ('REQUIRES_REVISION', 'Requires Revision'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(ShiftTask, on_delete=models.CASCADE, related_name='verification_records')
    submitted_by = models.ForeignKey('accounts.CustomUser', on_delete=models.CASCADE, related_name='submitted_verifications')
    verified_by = models.ForeignKey('accounts.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='verified_tasks')
    
    # Verification Evidence
    photo_evidence = models.JSONField(default=list, help_text="List of photo URLs/paths as evidence")
    document_evidence = models.JSONField(default=list, help_text="List of document URLs/paths as evidence")
    signature_data = models.TextField(blank=True, null=True, help_text="Digital signature data")
    checklist_responses = models.JSONField(default=dict, help_text="Responses to verification checklist items")
    
    # Temperature and Measurement Data
    temperature_readings = models.JSONField(default=list, help_text="Temperature readings with timestamps")
    quantity_counts = models.JSONField(default=dict, help_text="Quantity counts and measurements")
    measurement_data = models.JSONField(default=dict, help_text="Other measurement data")
    
    # Verification Status and Notes
    verification_status = models.CharField(max_length=20, choices=VERIFICATION_STATUS_CHOICES, default='PENDING')
    verification_notes = models.TextField(blank=True, null=True, help_text="Notes from verifier")
    rejection_reason = models.TextField(blank=True, null=True, help_text="Reason for rejection if applicable")
    
    # Timestamps
    submitted_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    
    # GPS and Location Data
    gps_coordinates = models.JSONField(default=dict, help_text="GPS coordinates where verification was submitted")
    location_verified = models.BooleanField(default=False, help_text="Whether location was verified")
    
    class Meta:
        db_table = 'task_verification_records'
        ordering = ['-submitted_at']
        indexes = [
            models.Index(fields=['task', 'verification_status']),
            models.Index(fields=['submitted_by', 'submitted_at']),
        ]
    
    def __str__(self):
        return f"Verification for {self.task.title} by {self.submitted_by.get_full_name()}"


class TaskComplianceCheck(models.Model):
    """Model for tracking compliance check completion"""
    CHECK_STATUS_CHOICES = (
        ('NOT_STARTED', 'Not Started'),
        ('IN_PROGRESS', 'In Progress'),
        ('PASSED', 'Passed'),
        ('FAILED', 'Failed'),
        ('REQUIRES_ATTENTION', 'Requires Attention'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(ShiftTask, on_delete=models.CASCADE, related_name='compliance_check_records')
    check_name = models.CharField(max_length=255, help_text="Name of the compliance check")
    check_description = models.TextField(help_text="Description of what needs to be checked")
    check_criteria = models.JSONField(default=dict, help_text="Specific criteria for passing this check")
    
    # Check Results
    status = models.CharField(max_length=20, choices=CHECK_STATUS_CHOICES, default='NOT_STARTED')
    result_data = models.JSONField(default=dict, help_text="Data collected during the check")
    pass_criteria_met = models.BooleanField(default=False)
    
    # Personnel
    checked_by = models.ForeignKey('accounts.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='performed_compliance_checks')
    supervisor_reviewed = models.BooleanField(default=False)
    supervisor_notes = models.TextField(blank=True, null=True)
    
    # Timestamps
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    # Regulatory Information
    regulation_reference = models.CharField(max_length=255, blank=True, null=True, help_text="Reference to specific regulation or standard")
    compliance_level = models.CharField(max_length=50, blank=True, null=True, help_text="Level of compliance required (e.g., FDA, OSHA)")
    
    class Meta:
        db_table = 'task_compliance_checks'
        ordering = ['check_name']
        indexes = [
            models.Index(fields=['task', 'status']),
            models.Index(fields=['status', 'completed_at']),
        ]
    
    def __str__(self):
        return f"{self.check_name} - {self.task.title}"


class Timesheet(models.Model):
    """Track staff work hours and earnings"""
    PAYROLL_STATUS_CHOICES = (
        ('DRAFT', 'Draft'),
        ('SUBMITTED', 'Submitted'),
        ('APPROVED', 'Approved'),
        ('PAID', 'Paid'),
        ('REJECTED', 'Rejected'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    staff = models.ForeignKey('accounts.CustomUser', on_delete=models.CASCADE, related_name='timesheets')
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='timesheets')
    start_date = models.DateField()
    end_date = models.DateField()
    total_hours = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    total_earnings = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    hourly_rate = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=PAYROLL_STATUS_CHOICES, default='DRAFT')
    notes = models.TextField(blank=True, null=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey('accounts.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_timesheets')
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'timesheets'
        unique_together = ['staff', 'start_date', 'end_date', 'restaurant']
        ordering = ['-end_date']
        indexes = [
            models.Index(fields=['staff', 'status']),
            models.Index(fields=['restaurant', 'status']),
            models.Index(fields=['end_date']),
        ]
    
    def __str__(self):
        return f"Timesheet for {self.staff.email} ({self.start_date} to {self.end_date})"
    
    def calculate_totals(self):
        """Recalculate total hours and earnings from shifts"""
        shifts = AssignedShift.objects.filter(
            staff=self.staff,
            shift_date__gte=self.start_date,
            shift_date__lte=self.end_date,
            status__in=['COMPLETED', 'CONFIRMED']
        )
        
        total_hours = sum(shift.actual_hours for shift in shifts)
        self.total_hours = total_hours
        self.total_earnings = total_hours * self.hourly_rate
        self.save()
    
    @property
    def is_editable(self):
        """Check if timesheet can still be edited"""
        return self.status in ['DRAFT', 'SUBMITTED']


class TimesheetEntry(models.Model):
    """Individual shift entry in a timesheet"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    timesheet = models.ForeignKey(Timesheet, on_delete=models.CASCADE, related_name='entries')
    shift = models.ForeignKey(AssignedShift, on_delete=models.CASCADE)
    hours_worked = models.DecimalField(max_digits=6, decimal_places=2)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'timesheet_entries'
        unique_together = ['timesheet', 'shift']
    
    def __str__(self):
        return f"Entry in {self.timesheet} - {self.shift.staff.email}"


class TemplateVersion(models.Model):
    """Version control for schedule templates"""
    VERSION_STATUS_CHOICES = (
        ('DRAFT', 'Draft'),
        ('ACTIVE', 'Active'),
        ('ARCHIVED', 'Archived'),
        ('DEPRECATED', 'Deprecated'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template = models.ForeignKey(ScheduleTemplate, on_delete=models.CASCADE, related_name='versions')
    version_number = models.CharField(max_length=20)
    status = models.CharField(max_length=20, choices=VERSION_STATUS_CHOICES, default='DRAFT')
    description = models.TextField(blank=True, null=True)
    changes_summary = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey('accounts.CustomUser', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    is_current = models.BooleanField(default=False)
    # Store template data as JSON for version history
    template_data = models.JSONField(default=dict, help_text="Snapshot of template configuration")
    shifts_data = models.JSONField(default=list, help_text="Snapshot of template shifts")
    
    class Meta:
        db_table = 'template_versions'
        unique_together = ['template', 'version_number']
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['template', 'status']),
            models.Index(fields=['status', 'created_at']),
        ]
    
    def __str__(self):
        return f"{self.template.name} v{self.version_number}"
    
# Ensure task template models are registered with Django
# This import loads models defined in scheduling/task_templates.py without circular imports
from . import task_templates  # noqa: F401
    