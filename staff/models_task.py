from django.db import models
from django.utils import timezone
import uuid
from accounts.models import CustomUser, Restaurant
from .models import Schedule

class StandardOperatingProcedure(models.Model):
    """Model for storing Standard Operating Procedures (SOPs)"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='sops')
    title = models.CharField(max_length=255)
    description = models.TextField()
    category = models.CharField(max_length=100)
    steps = models.JSONField(default=list)  # List of steps to follow
    safety_level = models.CharField(max_length=20, choices=[
        ('LOW', 'Low Risk'),
        ('MEDIUM', 'Medium Risk'),
        ('HIGH', 'High Risk'),
        ('CRITICAL', 'Critical Risk'),
    ], default='MEDIUM')
    required_ppe = models.JSONField(default=list)  # List of required PPE
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, related_name='created_sops')
    
    def __str__(self):
        return f"{self.title} - {self.restaurant.name}"
    
    class Meta:
        ordering = ['title']
        indexes = [
            models.Index(fields=['restaurant', 'category']),
            models.Index(fields=['safety_level']),
        ]

class SafetyChecklist(models.Model):
    """Model for safety checklists"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='safety_checklists')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    items = models.JSONField(default=list)  # List of checklist items
    category = models.CharField(max_length=100)  # e.g., 'Pre-shift', 'Equipment', 'Sanitation'
    frequency = models.CharField(max_length=50)  # e.g., 'Daily', 'Weekly', 'Before each use'
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.title} - {self.restaurant.name}"
    
    class Meta:
        ordering = ['title']
        indexes = [
            models.Index(fields=['restaurant', 'category']),
            models.Index(fields=['frequency']),
        ]

class ScheduleTask(models.Model):
    """Model for tasks assigned to schedules"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    schedule = models.ForeignKey(Schedule, on_delete=models.CASCADE, related_name='schedule_tasks')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    sop = models.ForeignKey(StandardOperatingProcedure, on_delete=models.SET_NULL, null=True, blank=True, related_name='tasks')
    checklist = models.ForeignKey(SafetyChecklist, on_delete=models.SET_NULL, null=True, blank=True, related_name='tasks')
    priority = models.CharField(max_length=20, choices=[
        ('LOW', 'Low'),
        ('MEDIUM', 'Medium'),
        ('HIGH', 'High'),
        ('CRITICAL', 'Critical'),
    ], default='MEDIUM')
    status = models.CharField(max_length=20, choices=[
        ('PENDING', 'Pending'),
        ('IN_PROGRESS', 'In Progress'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
    ], default='PENDING')
    due_time = models.DateTimeField(null=True, blank=True)
    completion_time = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='completed_tasks')
    completion_notes = models.TextField(blank=True, null=True)
    completion_evidence = models.ImageField(upload_to='task_evidence/', null=True, blank=True)
    
    def __str__(self):
        return f"{self.title} - {self.schedule.title}"
    
    class Meta:
        ordering = ['priority', 'due_time']
        indexes = [
            models.Index(fields=['schedule', 'status']),
            models.Index(fields=['priority']),
        ]

class SafetyConcernReport(models.Model):
    """Model for anonymous safety concern reporting"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='safety_concerns')
    # Incident metadata (for agent/voice-note reporting)
    incident_type = models.CharField(max_length=100, blank=True, default='General')
    title = models.CharField(max_length=255)
    description = models.TextField()
    location = models.CharField(max_length=255, blank=True, null=True)
    severity = models.CharField(max_length=20, choices=[
        ('LOW', 'Low'),
        ('MEDIUM', 'Medium'),
        ('HIGH', 'High'),
        ('CRITICAL', 'Critical'),
    ], default='MEDIUM')
    occurred_at = models.DateTimeField(null=True, blank=True)
    shift = models.ForeignKey('scheduling.AssignedShift', on_delete=models.SET_NULL, null=True, blank=True, related_name='safety_concerns')
    is_anonymous = models.BooleanField(default=True)
    reporter = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='reported_concerns')
    status = models.CharField(max_length=20, choices=[
        ('REPORTED', 'Reported'),
        ('UNDER_REVIEW', 'Under Review'),
        ('ADDRESSED', 'Addressed'),
        ('RESOLVED', 'Resolved'),
        ('DISMISSED', 'Dismissed'),
    ], default='REPORTED')
    photo = models.ImageField(upload_to='safety_concerns/', null=True, blank=True)
    audio_evidence = models.JSONField(default=list, blank=True)  # URLs to uploaded audio files (e.g., WhatsApp media URL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='resolved_concerns')
    resolution_notes = models.TextField(blank=True, null=True)
    
    def __str__(self):
        return f"{self.title} - {self.severity} - {self.status}"
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['restaurant', 'status']),
            models.Index(fields=['severity']),
        ]

class SafetyRecognition(models.Model):
    """Model for recognizing safety excellence"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    staff = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='safety_recognitions')
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='safety_recognitions')
    title = models.CharField(max_length=255)
    description = models.TextField()
    recognition_type = models.CharField(max_length=50)  # e.g., 'Safety Champion', 'Hazard Spotter'
    points = models.IntegerField(default=0)  # Points awarded for gamification
    awarded_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, related_name='awarded_recognitions')
    awarded_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.staff.get_full_name()} - {self.title}"
    
    class Meta:
        ordering = ['-awarded_at']
        indexes = [
            models.Index(fields=['staff', 'recognition_type']),
            models.Index(fields=['restaurant']),
        ]