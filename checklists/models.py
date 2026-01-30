"""
Checklist Models for Restaurant Management System
Extends the existing task system with structured checklist execution
"""
from django.db import models
from django.conf import settings
from django.utils import timezone
import uuid
import json


class ChecklistTemplate(models.Model):
    """Template for creating checklists"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='checklist_templates')
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    category = models.CharField(max_length=100, blank=True, null=True)
    
    # Link to task template for integration
    task_template = models.ForeignKey('scheduling.TaskTemplate', on_delete=models.SET_NULL, null=True, blank=True, related_name='checklist_templates')
    
    # Template metadata
    version = models.CharField(max_length=20, default='1.0')
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='created_checklist_templates')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Execution settings
    estimated_duration = models.DurationField(null=True, blank=True)
    requires_supervisor_approval = models.BooleanField(default=False)
    
    class Meta:
        db_table = 'checklist_templates'
        ordering = ['name']
        indexes = [
            models.Index(fields=['restaurant', 'category']),
            models.Index(fields=['is_active']),
        ]
    
    def __str__(self):
        return f"{self.name} ({self.restaurant.name})"


class ChecklistStep(models.Model):
    """
    Individual step within a checklist template
    """
    STEP_TYPES = (
        ('CHECK', 'Simple Check'),
        ('MEASUREMENT', 'Measurement Input'),
        ('PHOTO', 'Photo Evidence'),
        ('SIGNATURE', 'Digital Signature'),
        ('NOTE', 'Text Note'),
        ('CONDITIONAL', 'Conditional Logic'),
    )
    
    MEASUREMENT_TYPES = (
        ('TEMPERATURE', 'Temperature'),
        ('COUNT', 'Count/Quantity'),
        ('TIME', 'Time Duration'),
        ('WEIGHT', 'Weight'),
        ('VOLUME', 'Volume'),
        ('PERCENTAGE', 'Percentage'),
        ('CUSTOM', 'Custom Unit'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template = models.ForeignKey(ChecklistTemplate, on_delete=models.CASCADE, related_name='steps')
    
    # Step content
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    step_type = models.CharField(max_length=20, choices=STEP_TYPES)
    order = models.PositiveIntegerField()
    
    # Requirements
    is_required = models.BooleanField(default=True)
    requires_photo = models.BooleanField(default=False)
    requires_note = models.BooleanField(default=False)
    requires_signature = models.BooleanField(default=False)
    
    # Measurement settings
    measurement_type = models.CharField(max_length=20, choices=MEASUREMENT_TYPES, blank=True, null=True)
    measurement_unit = models.CharField(max_length=50, blank=True, null=True)
    min_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    max_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    target_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    
    # Conditional logic
    conditional_logic = models.JSONField(default=dict, blank=True, help_text="JSON defining conditional step logic")
    depends_on_step = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='dependent_steps')
    
    # Validation rules
    validation_rules = models.JSONField(default=dict, blank=True, help_text="JSON defining validation rules")
    
    class Meta:
        db_table = 'checklist_steps'
        ordering = ['template', 'order']
        unique_together = ['template', 'order']
        indexes = [
            models.Index(fields=['template', 'order']),
        ]
    
    def __str__(self):
        return f"{self.template.name} - Step {self.order}: {self.title}"


class ChecklistExecution(models.Model):
    """
    Instance of a checklist being executed
    """
    STATUS_CHOICES = (
        ('NOT_STARTED', 'Not Started'),
        ('IN_PROGRESS', 'In Progress'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
        ('FAILED', 'Failed'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template = models.ForeignKey(ChecklistTemplate, on_delete=models.CASCADE, related_name='executions')
    
    # Assignment and context
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='assigned_checklists')
    assigned_shift = models.ForeignKey('scheduling.AssignedShift', on_delete=models.SET_NULL, null=True, blank=True, related_name='checklists')
    task = models.ForeignKey('scheduling.ShiftTask', on_delete=models.SET_NULL, null=True, blank=True, related_name='checklist_executions')
    
    # Status and timing
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='NOT_STARTED')
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    due_date = models.DateTimeField(null=True, blank=True)
    
    # Progress tracking
    current_step = models.ForeignKey(ChecklistStep, on_delete=models.SET_NULL, null=True, blank=True, related_name='current_executions')
    progress_percentage = models.IntegerField(default=0)
    
    # Completion data
    completion_notes = models.TextField(blank=True, null=True)
    supervisor_approved = models.BooleanField(default=False)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_checklists')
    approved_at = models.DateTimeField(null=True, blank=True)
    
    analysis_results = models.JSONField(default=dict, blank=True, help_text="AI or automated analysis of the checklist execution results")
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Offline sync support
    last_synced_at = models.DateTimeField(null=True, blank=True)
    sync_version = models.IntegerField(default=1)
    
    class Meta:
        db_table = 'checklist_executions'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['assigned_to', 'status']),
            models.Index(fields=['template', 'status']),
            models.Index(fields=['due_date']),
        ]
    
    def __str__(self):
        return f"{self.template.name} - {self.assigned_to.get_full_name()} ({self.status})"
    
    def start_execution(self):
        """Start the checklist execution"""
        if self.status == 'NOT_STARTED':
            self.status = 'IN_PROGRESS'
            self.started_at = timezone.now()
            self.save()
    
    def complete_execution(self, completion_notes=None):
        """Complete the checklist execution"""
        if self.status == 'IN_PROGRESS':
            self.status = 'COMPLETED'
            self.completed_at = timezone.now()
            self.progress_percentage = 100
            if completion_notes:
                self.completion_notes = completion_notes
            self.save()
    
    def calculate_progress(self):
        """Calculate progress based on completed steps"""
        total_steps = self.step_responses.count()
        completed_steps = self.step_responses.filter(is_completed=True).count()
        if total_steps > 0:
            self.progress_percentage = int((completed_steps / total_steps) * 100)
            self.save()
        return self.progress_percentage


class ChecklistStepResponse(models.Model):
    """
    Response/completion data for a specific step in a checklist execution
    """
    RESPONSE_STATUS = (
        ('PENDING', 'Pending'),
        ('COMPLETED', 'Completed'),
        ('SKIPPED', 'Skipped'),
        ('FAILED', 'Failed'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    execution = models.ForeignKey(ChecklistExecution, on_delete=models.CASCADE, related_name='step_responses')
    step = models.ForeignKey(ChecklistStep, on_delete=models.CASCADE, related_name='responses')
    
    # Response data
    is_completed = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=RESPONSE_STATUS, default='PENDING')
    
    # Response content
    text_response = models.TextField(blank=True, null=True)
    measurement_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    boolean_response = models.BooleanField(null=True, blank=True)
    
    # Evidence and signatures
    notes = models.TextField(blank=True, null=True)
    signature_data = models.TextField(blank=True, null=True, help_text="Base64 encoded signature image")
    
    # Timing
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'checklist_step_responses'
        unique_together = ['execution', 'step']
        indexes = [
            models.Index(fields=['execution', 'step']),
            models.Index(fields=['status']),
        ]
    
    def __str__(self):
        return f"{self.execution.template.name} - {self.step.title} ({self.status})"


class ChecklistEvidence(models.Model):
    """
    Evidence attachments for checklist steps (photos, documents, etc.)
    """
    EVIDENCE_TYPES = (
        ('PHOTO', 'Photo'),
        ('VIDEO', 'Video'),
        ('DOCUMENT', 'Document'),
        ('AUDIO', 'Audio Recording'),
    )
    
    VISIBILITY_CHOICES = (
        ('PRIVATE', 'Private'),
        ('TEAM', 'Team'),
        ('ORGANIZATION', 'Organization'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    step_response = models.ForeignKey(ChecklistStepResponse, on_delete=models.CASCADE, related_name='evidence')
    
    # Evidence metadata
    evidence_type = models.CharField(max_length=20, choices=EVIDENCE_TYPES)
    filename = models.CharField(max_length=255)
    file_size = models.BigIntegerField()
    mime_type = models.CharField(max_length=100)
    
    # File storage
    file_path = models.CharField(max_length=500, help_text="Path to stored file")
    thumbnail_path = models.CharField(max_length=500, blank=True, null=True, help_text="Path to thumbnail if applicable")
    
    # Metadata
    visibility = models.CharField(max_length=20, choices=VISIBILITY_CHOICES, default='TEAM')
    metadata = models.JSONField(default=dict, blank=True, help_text="Additional metadata like GPS, timestamp, etc.")
    
    # Timestamps
    captured_at = models.DateTimeField(default=timezone.now)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'checklist_evidence'
        ordering = ['-captured_at']
        indexes = [
            models.Index(fields=['step_response']),
            models.Index(fields=['evidence_type']),
        ]
    
    def __str__(self):
        return f"{self.evidence_type}: {self.filename}"


class ChecklistAction(models.Model):
    """
    Action items created during checklist execution
    """
    PRIORITY_CHOICES = (
        ('LOW', 'Low'),
        ('MEDIUM', 'Medium'),
        ('HIGH', 'High'),
        ('URGENT', 'Urgent'),
    )
    
    STATUS_CHOICES = (
        ('OPEN', 'Open'),
        ('IN_PROGRESS', 'In Progress'),
        ('RESOLVED', 'Resolved'),
        ('CANCELLED', 'Cancelled'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    execution = models.ForeignKey(ChecklistExecution, on_delete=models.CASCADE, related_name='actions')
    step_response = models.ForeignKey(ChecklistStepResponse, on_delete=models.CASCADE, null=True, blank=True, related_name='actions')
    
    # Action details
    title = models.CharField(max_length=255)
    description = models.TextField()
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='MEDIUM')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='OPEN')
    
    # Assignment
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_checklist_actions')
    due_date = models.DateTimeField(null=True, blank=True)
    
    # Resolution
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='resolved_checklist_actions')
    resolution_notes = models.TextField(blank=True, null=True)
    
    # Metadata
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='created_checklist_actions')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'checklist_actions'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['execution']),
            models.Index(fields=['assigned_to', 'status']),
            models.Index(fields=['priority', 'status']),
        ]
    
    def __str__(self):
        return f"Action: {self.title} ({self.priority})"