"""
Task Templates Module for Restaurant Management System
Provides pre-built task templates and task management functionality
"""
from django.db import models
import uuid
from django.utils import timezone
from django.conf import settings
from .models import TaskCategory

class TaskTemplate(models.Model):
    """Base model for task templates"""
    TEMPLATE_TYPES = (
        ('CLEANING', 'Daily Restaurant Cleaning Schedule'),
        ('TEMPERATURE', 'Daily Temperature Log'),
        ('OPENING', 'Restaurant Manager Opening Checklist'),
        ('CLOSING', 'Restaurant Manager Closing Checklist'),
        ('HEALTH', 'Monthly Health and Safety Inspection'),
        ('SOP', 'Standard Operating Procedure'),
        ('CUSTOM', 'Custom Template'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='task_templates')
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    template_type = models.CharField(max_length=20, choices=TEMPLATE_TYPES)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='created_templates')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Template content stored as JSON
    tasks = models.JSONField(default=list)
    
    # Frequency settings
    FREQUENCY_CHOICES = (
        ('DAILY', 'Daily'),
        ('WEEKLY', 'Weekly'),
        ('MONTHLY', 'Monthly'),
        ('CUSTOM', 'Custom'),
    )
    frequency = models.CharField(max_length=20, choices=FREQUENCY_CHOICES, default='DAILY')
    
    # AI-related fields
    ai_generated = models.BooleanField(default=False)
    ai_prompt = models.TextField(blank=True, null=True)
    
    class Meta:
        db_table = 'task_templates'
        ordering = ['name']
        indexes = [
            models.Index(fields=['restaurant', 'template_type']),
            models.Index(fields=['frequency']),
        ]
    
    def __str__(self):
        return f"{self.name} - {self.restaurant.name}"
    
    def duplicate(self):
        """Create a copy of this template"""
        new_template = TaskTemplate.objects.create(
            restaurant=self.restaurant,
            name=f"Copy of {self.name}",
            description=self.description,
            template_type=self.template_type,
            tasks=self.tasks,
            frequency=self.frequency,
            created_by=self.created_by
        )
        return new_template



class Task(models.Model):
    """Model for individual tasks"""
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
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='tasks')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    category = models.ForeignKey(TaskCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='tasks')
    template = models.ForeignKey(TaskTemplate, on_delete=models.SET_NULL, null=True, blank=True, related_name='generated_tasks')
    
    # Assignment fields
    assigned_to = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='assigned_tasks')
    assigned_shift = models.ForeignKey('scheduling.AssignedShift', on_delete=models.SET_NULL, null=True, blank=True, related_name='tasks')
    
    # Status fields
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='MEDIUM')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='TODO')
    progress = models.IntegerField(default=0)  # 0-100%
    
    # Time fields
    due_date = models.DateField(null=True, blank=True)
    due_time = models.TimeField(null=True, blank=True)
    estimated_duration = models.DurationField(null=True, blank=True)
    
    # Completion fields
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='completed_tasks')
    completion_notes = models.TextField(blank=True, null=True)
    completion_evidence = models.ImageField(upload_to='task_evidence/', null=True, blank=True)
    
    # Subtask support
    parent_task = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='subtasks')
    
    # Metadata
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='created_tasks')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Reminder settings
    reminder_enabled = models.BooleanField(default=True)
    reminder_time = models.DurationField(default=timezone.timedelta(hours=1))  # Time before due date/time
    
    class Meta:
        db_table = 'tasks'
        ordering = ['due_date', 'due_time', 'priority']
        indexes = [
            models.Index(fields=['restaurant', 'status']),
            models.Index(fields=['due_date']),
            models.Index(fields=['priority']),
        ]
    
    def __str__(self):
        return f"{self.title} - {self.get_status_display()}"
    
    def mark_completed(self, user=None):
        """Mark task as completed"""
        self.status = 'COMPLETED'
        self.progress = 100
        self.completed_at = timezone.now()
        if user:
            self.completed_by = user
        self.save()
    
    def start_task(self, user=None):
        """Mark task as in progress"""
        self.status = 'IN_PROGRESS'
        if self.progress == 0:
            self.progress = 10
        self.save()
    
    @property
    def is_overdue(self):
        """Check if task is overdue"""
        if not self.due_date:
            return False
        if self.status in ['COMPLETED', 'CANCELLED']:
            return False
        today = timezone.now().date()
        return self.due_date < today