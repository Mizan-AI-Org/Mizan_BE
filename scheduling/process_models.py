import uuid
from django.db import models
from django.utils import timezone
from django.conf import settings

# Reuse audit mixin for automatic logging
from .audit import AuditMixin


class ProcessStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    ACTIVE = "ACTIVE", "Active"
    PAUSED = "PAUSED", "Paused"
    ARCHIVED = "ARCHIVED", "Archived"


class ProcessPriority(models.TextChoices):
    LOW = "LOW", "Low"
    MEDIUM = "MEDIUM", "Medium"
    HIGH = "HIGH", "High"
    URGENT = "URGENT", "Urgent"


class Process(AuditMixin, models.Model):
    """Represents a business process comprising multiple tasks.

    Keeps the data model intentionally lean and consistent with existing Task/Template patterns.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='processes')

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)

    status = models.CharField(max_length=20, choices=ProcessStatus.choices, default=ProcessStatus.DRAFT)
    priority = models.CharField(max_length=20, choices=ProcessPriority.choices, default=ProcessPriority.MEDIUM)
    is_active = models.BooleanField(default=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_processes')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='updated_processes')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Optional metadata and SOP hooks, aligned with template/task fields
    sop_document = models.TextField(blank=True, null=True)
    sop_steps = models.JSONField(default=list)
    is_critical = models.BooleanField(default=False)

    class Meta:
        db_table = 'processes'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['restaurant', 'status']),
            models.Index(fields=['is_active']),
        ]

    def __str__(self):
        return f"{self.name} - {self.restaurant.name}"


class ProcessTaskStatus(models.TextChoices):
    TODO = "TODO", "To Do"
    IN_PROGRESS = "IN_PROGRESS", "In Progress"
    COMPLETED = "COMPLETED", "Completed"
    CANCELLED = "CANCELLED", "Cancelled"


class VerificationTypes(models.TextChoices):
    NONE = 'NONE', 'No Verification Required'
    PHOTO = 'PHOTO', 'Photo Evidence Required'
    DOCUMENT = 'DOCUMENT', 'Document Upload Required'
    SIGNATURE = 'SIGNATURE', 'Digital Signature Required'
    CHECKLIST = 'CHECKLIST', 'Checklist Completion Required'
    SUPERVISOR_APPROVAL = 'SUPERVISOR_APPROVAL', 'Supervisor Approval Required'


class ProcessTask(AuditMixin, models.Model):
    """Task that belongs to a Process. Similar to existing Task, lighter than ShiftTask."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    process = models.ForeignKey(Process, on_delete=models.CASCADE, related_name='tasks')
    category = models.ForeignKey('scheduling.TaskCategory', on_delete=models.SET_NULL, null=True, blank=True, related_name='process_tasks')

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    priority = models.CharField(max_length=20, choices=ProcessPriority.choices, default=ProcessPriority.MEDIUM)
    status = models.CharField(max_length=20, choices=ProcessTaskStatus.choices, default=ProcessTaskStatus.TODO)

    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_process_tasks')
    due_date = models.DateField(null=True, blank=True)
    due_time = models.TimeField(null=True, blank=True)
    estimated_duration = models.DurationField(null=True, blank=True)

    verification_type = models.CharField(max_length=30, choices=VerificationTypes.choices, default=VerificationTypes.NONE)
    verification_required = models.BooleanField(default=False)

    # Hierarchy within a process
    parent_task = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='subtasks')

    # Tracking
    progress = models.IntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completion_notes = models.TextField(blank=True, null=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_process_tasks')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='updated_process_tasks')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'process_tasks'
        ordering = ['due_date', 'priority']
        indexes = [
            models.Index(fields=['process', 'status']),
            models.Index(fields=['assigned_to', 'status']),
        ]

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"

    def mark_completed(self, notes: str | None = None):
        self.status = ProcessTaskStatus.COMPLETED
        self.completed_at = timezone.now()
        if notes:
            self.completion_notes = notes
        self.progress = 100
        self.save()