"""
Task Templates Module for Restaurant Management System
Provides pre-built task templates and task management functionality
"""
from django.db import models
import uuid
from django.utils import timezone
from django.conf import settings
# Avoid circular import by using string model reference for TaskCategory

class TaskTemplate(models.Model):
    """Base model for task templates"""
    TEMPLATE_TYPES = (
        ('CLEANING', 'Daily Restaurant Cleaning Schedule'),
        ('TEMPERATURE', 'Daily Temperature Log'),
        ('OPENING', 'Restaurant Manager Opening Checklist'),
        ('CLOSING', 'Restaurant Manager Closing Checklist'),
        ('HEALTH', 'Monthly Health and Safety Inspection'),
        ('SOP', 'Standard Operating Procedure'),
        ('MAINTENANCE', 'Equipment Maintenance'),
        ('COMPLIANCE', 'Compliance Check'),
        ('SAFETY', 'Safety Protocol'),
        ('QUALITY', 'Quality Control'),
        ('CUSTOM', 'Custom Template'),
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
        ('QUARTERLY', 'Quarterly'),
        ('ANNUALLY', 'Annually'),
        ('CUSTOM', 'Custom'),
    )
    frequency = models.CharField(max_length=20, choices=FREQUENCY_CHOICES, default='DAILY')
    
    # AI-related fields
    ai_generated = models.BooleanField(default=False)
    ai_prompt = models.TextField(blank=True, null=True)
    
    # Enhanced SOP and Compliance Fields
    sop_document = models.TextField(blank=True, null=True, help_text="Standard Operating Procedure instructions")
    sop_steps = models.JSONField(default=list, help_text="Step-by-step SOP instructions as JSON array")
    sop_version = models.CharField(max_length=20, blank=True, null=True, help_text="SOP version number")
    sop_last_updated = models.DateTimeField(null=True, blank=True, help_text="When SOP was last updated")
    
    # Compliance and Safety Requirements
    compliance_checks = models.JSONField(default=list, help_text="List of required compliance checks")
    safety_requirements = models.JSONField(default=list, help_text="Safety requirements and protocols")
    quality_standards = models.JSONField(default=list, help_text="Quality control standards to meet")
    regulatory_requirements = models.JSONField(default=list, help_text="Regulatory compliance requirements")
    
    # Verification Requirements
    verification_type = models.CharField(max_length=30, choices=VERIFICATION_TYPES, default='NONE')
    verification_required = models.BooleanField(default=False)
    verification_instructions = models.TextField(blank=True, null=True, help_text="Instructions for verification process")
    verification_checklist = models.JSONField(default=list, help_text="Checklist items for verification")
    
    # Equipment and Skills Requirements
    required_equipment = models.JSONField(default=list, help_text="List of required equipment/tools")
    required_materials = models.JSONField(default=list, help_text="List of required materials/supplies")
    required_skills = models.JSONField(default=list, help_text="Skills required to complete tasks in this template")
    required_certifications = models.JSONField(default=list, help_text="Certifications required for tasks in this template")
    
    # Template Metadata
    estimated_duration = models.DurationField(null=True, blank=True, help_text="Estimated time to complete all tasks")
    difficulty_level = models.CharField(max_length=20, choices=[
        ('BEGINNER', 'Beginner'),
        ('INTERMEDIATE', 'Intermediate'),
        ('ADVANCED', 'Advanced'),
        ('EXPERT', 'Expert')
    ], default='INTERMEDIATE')
    
    # Critical and Priority Settings
    is_critical = models.BooleanField(default=False, help_text="Mark as critical template requiring immediate attention")
    priority_level = models.CharField(max_length=20, choices=[
        ('LOW', 'Low'),
        ('MEDIUM', 'Medium'),
        ('HIGH', 'High'),
        ('URGENT', 'Urgent')
    ], default='MEDIUM')
    
    # Version Control
    version = models.CharField(max_length=20, default='1.0', help_text="Template version number")
    parent_template = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='versions')
    is_latest_version = models.BooleanField(default=True)
    
    # Approval and Review
    requires_approval = models.BooleanField(default=False, help_text="Whether tasks from this template require supervisor approval")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_templates')
    approved_at = models.DateTimeField(null=True, blank=True)
    
    # Usage Statistics
    usage_count = models.IntegerField(default=0, help_text="Number of times this template has been used")
    last_used = models.DateTimeField(null=True, blank=True, help_text="When this template was last used")
    
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
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='template_tasks')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    category = models.ForeignKey('scheduling.TaskCategory', on_delete=models.SET_NULL, null=True, blank=True, related_name='template_tasks')
    template = models.ForeignKey(TaskTemplate, on_delete=models.SET_NULL, null=True, blank=True, related_name='generated_tasks')
    
    # Assignment fields
    assigned_to = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='template_assigned_tasks')
    assigned_shift = models.ForeignKey('scheduling.AssignedShift', on_delete=models.SET_NULL, null=True, blank=True, related_name='template_tasks')
    
    # Task properties
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='MEDIUM')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='TODO')
    progress = models.IntegerField(default=0)  # 0-100%
    
    # Timing fields
    due_date = models.DateField(null=True, blank=True)
    due_time = models.DateTimeField(null=True, blank=True)
    estimated_duration = models.DurationField(null=True, blank=True)
    
    # Completion tracking
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='template_completed_tasks')
    completion_notes = models.TextField(blank=True, null=True)
    completion_evidence = models.ImageField(upload_to='task_evidence/', null=True, blank=True)
    
    # Task hierarchy
    parent_task = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='subtasks')
    
    # Audit fields
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='template_created_tasks')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Reminder settings
    reminder_enabled = models.BooleanField(default=True)
    reminder_time = models.DurationField(default=timezone.timedelta(hours=1))  # Time before due date/time
    
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
    
    # Critical Task Indicators
    is_critical = models.BooleanField(default=False, help_text="Mark as critical task requiring immediate attention")
    supervisor_notification_required = models.BooleanField(default=False, help_text="Notify supervisor when task status changes")
    
    # Completion Tracking Enhancement
    actual_duration = models.DurationField(null=True, blank=True, help_text="Actual time taken to complete task")
    started_at = models.DateTimeField(null=True, blank=True, help_text="When task was started")
    
    # Dependencies
    depends_on_tasks = models.ManyToManyField('self', symmetrical=False, blank=True, related_name='dependent_tasks', help_text="Tasks that must be completed before this one")
    
    # Location and Context
    location_specific = models.CharField(max_length=255, blank=True, null=True, help_text="Specific location where task should be performed")
    environmental_conditions = models.JSONField(default=dict, help_text="Required environmental conditions (temperature, humidity, etc.)")
    
    # Enhanced tracking fields
    progress_percentage = models.IntegerField(default=0, help_text="Progress percentage (0-100)")
    progress_notes = models.TextField(blank=True, null=True, help_text="Notes about current progress")
    last_updated = models.DateTimeField(auto_now=True, help_text="When task was last updated")
    checkpoints = models.JSONField(default=list, help_text="Progress checkpoints with timestamps and photos")
    completion_photo = models.ImageField(upload_to='task_completions/', null=True, blank=True, help_text="Photo evidence of task completion")
    completion_location = models.CharField(max_length=255, blank=True, null=True, help_text="Location where task was completed")
    
    # Recurring task settings
    is_recurring = models.BooleanField(default=False)
    recurrence_pattern = models.CharField(max_length=50, blank=True, null=True, help_text="Pattern for recurring tasks (daily, weekly, etc.)")
    next_occurrence = models.DateTimeField(null=True, blank=True, help_text="When this task should next occur")
    
    class Meta:
        db_table = 'template_tasks'
        ordering = ['due_date', 'due_time', 'priority']
        indexes = [
            models.Index(fields=['restaurant', 'status']),
            models.Index(fields=['due_date']),
            models.Index(fields=['priority']),
        ]
    
    def __str__(self):
        return f"{self.title} - {self.get_status_display()}"
    
    def mark_completed(self, user=None, completion_photo=None, completion_location=None, completion_notes=None):
        """Mark task as completed with optional photo and location evidence"""
        self.status = 'COMPLETED'
        self.progress = 100
        self.progress_percentage = 100
        self.completed_at = timezone.now()
        if user:
            self.completed_by = user
        if completion_photo:
            self.completion_photo = completion_photo
        if completion_location:
            self.completion_location = completion_location
        if completion_notes:
            self.completion_notes = completion_notes
        self.save()
    
    def start_task(self, user=None):
        """Mark task as in progress"""
        self.status = 'IN_PROGRESS'
        if self.progress == 0:
            self.progress = 10
        if self.progress_percentage == 0:
            self.progress_percentage = 10
        if not self.started_at:
            self.started_at = timezone.now()
        self.save()
    
    def update_progress(self, percentage, notes=None, checkpoint_photo=None, user=None):
        """Update task progress with optional checkpoint photo and notes"""
        self.progress_percentage = min(100, max(0, percentage))
        self.progress = self.progress_percentage  # Keep both fields in sync
        
        if notes:
            self.progress_notes = notes
        
        # Add checkpoint to history
        checkpoint = {
            'timestamp': timezone.now().isoformat(),
            'percentage': percentage,
            'notes': notes or '',
            'user': user.username if user else None,
        }
        
        if checkpoint_photo:
            # In a real implementation, you'd save the photo and store the URL
            checkpoint['photo_url'] = str(checkpoint_photo)
        
        if not isinstance(self.checkpoints, list):
            self.checkpoints = []
        
        self.checkpoints.append(checkpoint)
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