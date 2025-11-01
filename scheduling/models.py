from django.db import models
import uuid
from django.utils import timezone
from django.conf import settings
from django.core.exceptions import ValidationError

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
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shift = models.ForeignKey(AssignedShift, on_delete=models.CASCADE, related_name='tasks')
    category = models.ForeignKey(TaskCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='tasks')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='MEDIUM')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='TODO')
    assigned_to = models.ForeignKey('accounts.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='shift_assigned_tasks')
    estimated_duration = models.DurationField(null=True, blank=True)  # Time estimate
    parent_task = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='subtasks')
    notes = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey('accounts.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='created_tasks')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
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
    