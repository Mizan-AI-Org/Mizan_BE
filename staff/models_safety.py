from django.db import models
from django.utils import timezone
import uuid
from accounts.models import CustomUser, Restaurant
from staff.models import Schedule

class StandardOperatingProcedure(models.Model):
    """Standard Operating Procedures with safety classifications"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    description = models.TextField()
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='sops')
    safety_level = models.CharField(
        max_length=20,
        choices=[
            ('LOW', 'Low Risk'),
            ('MEDIUM', 'Medium Risk'),
            ('HIGH', 'High Risk'),
            ('CRITICAL', 'Critical Risk')
        ],
        default='MEDIUM'
    )
    ppe_required = models.JSONField(default=list, blank=True)
    document_url = models.URLField(blank=True, null=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, related_name='created_sops')
    
    def __str__(self):
        return self.title

class SafetyChecklist(models.Model):
    """Safety checklists for various operations"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='safety_checklists')
    items = models.JSONField(default=list)  # List of checklist items
    frequency = models.CharField(
        max_length=20,
        choices=[
            ('DAILY', 'Daily'),
            ('SHIFT', 'Every Shift'),
            ('WEEKLY', 'Weekly'),
            ('MONTHLY', 'Monthly'),
            ('QUARTERLY', 'Quarterly'),
            ('CUSTOM', 'Custom')
        ],
        default='SHIFT'
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, related_name='created_checklists')
    
    def __str__(self):
        return self.title

class ScheduleTask(models.Model):
    """Tasks assigned to schedules with safety procedures"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    schedule = models.ForeignKey(Schedule, on_delete=models.CASCADE, related_name='assigned_tasks')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    sop = models.ForeignKey(StandardOperatingProcedure, on_delete=models.SET_NULL, null=True, blank=True, related_name='tasks')
    checklist = models.ForeignKey(SafetyChecklist, on_delete=models.SET_NULL, null=True, blank=True, related_name='tasks')
    priority = models.CharField(
        max_length=20,
        choices=[
            ('LOW', 'Low'),
            ('MEDIUM', 'Medium'),
            ('HIGH', 'High'),
            ('CRITICAL', 'Critical')
        ],
        default='MEDIUM'
    )
    status = models.CharField(
        max_length=20,
        choices=[
            ('PENDING', 'Pending'),
            ('IN_PROGRESS', 'In Progress'),
            ('COMPLETED', 'Completed'),
            ('CANCELLED', 'Cancelled')
        ],
        default='PENDING'
    )
    due_time = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='completed_tasks')
    completion_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    
    def __str__(self):
        return self.title

class SafetyConcernReport(models.Model):
    """Anonymous safety concern reporting system"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='safety_reports')
    title = models.CharField(max_length=255)
    description = models.TextField()
    location = models.CharField(max_length=255, blank=True)
    severity = models.CharField(
        max_length=20,
        choices=[
            ('LOW', 'Low'),
            ('MEDIUM', 'Medium'),
            ('HIGH', 'High'),
            ('CRITICAL', 'Critical')
        ],
        default='MEDIUM'
    )
    is_anonymous = models.BooleanField(default=True)
    reporter = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='safety_reports')
    status = models.CharField(
        max_length=20,
        choices=[
            ('REPORTED', 'Reported'),
            ('UNDER_REVIEW', 'Under Review'),
            ('ADDRESSED', 'Addressed'),
            ('RESOLVED', 'Resolved'),
            ('DISMISSED', 'Dismissed')
        ],
        default='REPORTED'
    )
    resolution_notes = models.TextField(blank=True)
    resolved_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='resolved_safety_reports')
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    
    def __str__(self):
        return self.title

class SafetyRecognition(models.Model):
    """Recognition system for safety excellence"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    staff = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='safety_recognitions')
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='safety_recognitions')
    title = models.CharField(max_length=255)
    description = models.TextField()
    points = models.IntegerField(default=10)  # Points awarded for gamification
    awarded_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, related_name='awarded_recognitions')
    created_at = models.DateTimeField(default=timezone.now)
    
    def __str__(self):
        return f"{self.title} - {self.staff.get_full_name()}"