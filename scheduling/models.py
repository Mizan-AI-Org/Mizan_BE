from django.db import models
import uuid
from django.utils import timezone
from django.conf import settings

class ScheduleTemplate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
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
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    schedule = models.ForeignKey(WeeklySchedule, on_delete=models.CASCADE, related_name='assigned_shifts')
    staff = models.ForeignKey('accounts.CustomUser', on_delete=models.CASCADE, related_name='assigned_shifts')
    shift_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    break_duration = models.DurationField(default=timezone.timedelta(minutes=30)) # Example default 30 min break
    role = models.CharField(max_length=20, choices=settings.STAFF_ROLES_CHOICES) # Assuming STAFF_ROLES_CHOICES in settings
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['schedule', 'staff', 'shift_date']
        ordering = ['shift_date', 'start_time']
    
    def __str__(self):
        return f'{self.staff.first_name} {self.staff.last_name} - {self.shift_date} ({self.start_time}-{self.end_time})'

    @property
    def actual_hours(self):
        # Combine date and time to create datetime objects for calculation
        shift_start_datetime = timezone.datetime.combine(self.shift_date, self.start_time)
        shift_end_datetime = timezone.datetime.combine(self.shift_date, self.end_time)
        
        # Handle overnight shifts
        if shift_end_datetime < shift_start_datetime:
            shift_end_datetime += timezone.timedelta(days=1)
        
        duration = shift_end_datetime - shift_start_datetime
        
        # Subtract break duration if it exists
        if self.break_duration:
            duration -= self.break_duration
            
        return duration.total_seconds() / 3600 # Return hours as a float

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
    