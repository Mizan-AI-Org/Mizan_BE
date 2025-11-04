"""
Django signals for automatic audit logging
Automatically tracks changes to scheduling and task models
"""

from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from threading import local
import json

from .models import (
    ScheduleTemplate, TemplateShift, WeeklySchedule, 
    AssignedShift, ShiftTask, ShiftSwapRequest
)
from .audit import AuditTrailService, AuditActionType, AuditSeverity

User = get_user_model()

# Thread-local storage for request context
_thread_locals = local()

def set_current_user(user):
    """Set the current user for audit logging"""
    _thread_locals.user = user

def set_current_request(request):
    """Set the current request for audit logging"""
    _thread_locals.request = request

def get_current_user():
    """Get the current user from thread-local storage"""
    return getattr(_thread_locals, 'user', None)

def get_current_request():
    """Get the current request from thread-local storage"""
    return getattr(_thread_locals, 'request', None)

def clear_audit_context():
    """Clear audit context"""
    if hasattr(_thread_locals, 'user'):
        delattr(_thread_locals, 'user')
    if hasattr(_thread_locals, 'request'):
        delattr(_thread_locals, 'request')

class AuditSignalMixin:
    """Mixin to handle common audit signal functionality"""
    
    @staticmethod
    def get_model_changes(sender, instance, **kwargs):
        """Get the changes made to a model instance"""
        if not hasattr(instance, '_state') or instance._state.adding:
            return None, None
        
        try:
            # Get the original instance from database
            original = sender.objects.get(pk=instance.pk)
            old_values = {}
            new_values = {}
            
            # Compare field values
            for field in instance._meta.fields:
                field_name = field.name
                old_value = getattr(original, field_name, None)
                new_value = getattr(instance, field_name, None)
                
                if old_value != new_value:
                    # Convert to JSON serializable format
                    old_values[field_name] = AuditSignalMixin._serialize_value(old_value)
                    new_values[field_name] = AuditSignalMixin._serialize_value(new_value)
            
            return old_values if old_values else None, new_values if new_values else None
        except sender.DoesNotExist:
            return None, None
    
    @staticmethod
    def _serialize_value(value):
        """Convert value to JSON serializable format"""
        if value is None:
            return None
        elif hasattr(value, 'isoformat'):  # datetime objects
            return value.isoformat()
        elif hasattr(value, '__str__'):
            return str(value)
        else:
            return value

# Schedule Template Signals
@receiver(post_save, sender=ScheduleTemplate)
def log_schedule_template_save(sender, instance, created, **kwargs):
    """Log schedule template creation and updates"""
    user = get_current_user()
    request = get_current_request()
    
    if created:
        AuditTrailService.log_activity(
            user=user,
            action=AuditActionType.CREATE,
            description=f"Created schedule template: {instance.name}",
            content_object=instance,
            severity=AuditSeverity.MEDIUM,
            metadata={
                'template_name': instance.name,
                'restaurant_id': instance.restaurant.id if instance.restaurant else None,
                'is_active': instance.is_active
            },
            request=request
        )
    else:
        old_values, new_values = AuditSignalMixin.get_model_changes(sender, instance, **kwargs)
        if old_values or new_values:
            AuditTrailService.log_activity(
                user=user,
                action=AuditActionType.UPDATE,
                description=f"Updated schedule template: {instance.name}",
                content_object=instance,
                old_values=old_values,
                new_values=new_values,
                severity=AuditSeverity.LOW,
                metadata={
                    'template_name': instance.name,
                    'changes_count': len(new_values) if new_values else 0
                },
                request=request
            )

@receiver(post_delete, sender=ScheduleTemplate)
def log_schedule_template_delete(sender, instance, **kwargs):
    """Log schedule template deletion"""
    user = get_current_user()
    request = get_current_request()
    
    AuditTrailService.log_activity(
        user=user,
        action=AuditActionType.DELETE,
        description=f"Deleted schedule template: {instance.name}",
        severity=AuditSeverity.HIGH,
        metadata={
            'template_name': instance.name,
            'template_id': instance.id,
            'restaurant_id': instance.restaurant.id if instance.restaurant else None
        },
        request=request
    )

# Template Shift Signals
@receiver(post_save, sender=TemplateShift)
def log_template_shift_save(sender, instance, created, **kwargs):
    """Log template shift creation and updates"""
    user = get_current_user()
    request = get_current_request()
    
    if created:
        AuditTrailService.log_activity(
            user=user,
            action=AuditActionType.CREATE,
            description=f"Created template shift for {instance.template.name}",
            content_object=instance,
            severity=AuditSeverity.LOW,
            metadata={
                'template_name': instance.template.name,
                'day_of_week': instance.day_of_week,
                'start_time': instance.start_time.isoformat() if instance.start_time else None,
                'end_time': instance.end_time.isoformat() if instance.end_time else None,
                'role': instance.role
            },
            request=request
        )

# Weekly Schedule Signals
@receiver(post_save, sender=WeeklySchedule)
def log_weekly_schedule_save(sender, instance, created, **kwargs):
    """Log weekly schedule creation and updates"""
    user = get_current_user()
    request = get_current_request()
    
    if created:
        AuditTrailService.log_schedule_activity(
            user=user,
            schedule=instance,
            action=AuditActionType.CREATE,
            description=f"Created weekly schedule for week {instance.week_start_date}",
            metadata={
                'week_start': instance.week_start_date.isoformat(),
                'restaurant_id': instance.restaurant.id if instance.restaurant else None,
                'is_published': instance.is_published
            },
            request=request
        )
    else:
        old_values, new_values = AuditSignalMixin.get_model_changes(sender, instance, **kwargs)
        if old_values or new_values:
            # Check if schedule was published
            if new_values and 'is_published' in new_values and new_values['is_published']:
                AuditTrailService.log_schedule_activity(
                    user=user,
                    schedule=instance,
                    action=AuditActionType.SCHEDULE_PUBLISH,
                    description=f"Published weekly schedule for week {instance.week_start_date}",
                    old_values=old_values,
                    new_values=new_values,
                    metadata={'week_start': instance.week_start_date.isoformat()},
                    request=request
                )
            else:
                AuditTrailService.log_schedule_activity(
                    user=user,
                    schedule=instance,
                    action=AuditActionType.UPDATE,
                    description=f"Updated weekly schedule for week {instance.week_start_date}",
                    old_values=old_values,
                    new_values=new_values,
                    request=request
                )

# Assigned Shift Signals
@receiver(post_save, sender=AssignedShift)
def log_assigned_shift_save(sender, instance, created, **kwargs):
    """Log assigned shift creation and updates"""
    user = get_current_user()
    request = get_current_request()
    
    if created:
        AuditTrailService.log_shift_activity(
            user=user,
            shift=instance,
            action=AuditActionType.ASSIGN,
            description=f"Assigned shift to {instance.staff.get_full_name() if instance.staff else 'Unknown'}",
            metadata={
                'staff_id': instance.staff.id if instance.staff else None,
                'staff_name': instance.staff.get_full_name() if instance.staff else None,
                'shift_date': instance.shift_date.isoformat(),
                'start_time': instance.start_time.isoformat() if instance.start_time else None,
                'end_time': instance.end_time.isoformat() if instance.end_time else None,
                'role': instance.role
            },
            request=request
        )
    else:
        old_values, new_values = AuditSignalMixin.get_model_changes(sender, instance, **kwargs)
        if old_values or new_values:
            # Check if staff was changed (reassignment)
            if new_values and 'staff' in new_values:
                AuditTrailService.log_shift_activity(
                    user=user,
                    shift=instance,
                    action=AuditActionType.TASK_REASSIGN,
                    description=f"Reassigned shift from {old_values.get('staff', 'Unknown')} to {new_values.get('staff', 'Unknown')}",
                    old_values=old_values,
                    new_values=new_values,
                    severity=AuditSeverity.MEDIUM,
                    request=request
                )
            else:
                AuditTrailService.log_shift_activity(
                    user=user,
                    shift=instance,
                    action=AuditActionType.UPDATE,
                    description=f"Updated assigned shift for {instance.staff.get_full_name() if instance.staff else 'Unknown'}",
                    old_values=old_values,
                    new_values=new_values,
                    request=request
                )

@receiver(post_delete, sender=AssignedShift)
def log_assigned_shift_delete(sender, instance, **kwargs):
    """Log assigned shift deletion"""
    user = get_current_user()
    request = get_current_request()
    
    AuditTrailService.log_shift_activity(
        user=user,
        shift=instance,
        action=AuditActionType.UNASSIGN,
        description=f"Removed shift assignment for {instance.staff.get_full_name() if instance.staff else 'Unknown'}",
        severity=AuditSeverity.MEDIUM,
        metadata={
            'staff_id': instance.staff.id if instance.staff else None,
            'staff_name': instance.staff.get_full_name() if instance.staff else None,
            'shift_date': instance.shift_date.isoformat(),
            'role': instance.role
        },
        request=request
    )

# Shift Task Signals
@receiver(post_save, sender=ShiftTask)
def log_shift_task_save(sender, instance, created, **kwargs):
    """Log shift task creation and updates"""
    user = get_current_user()
    request = get_current_request()
    
    if created:
        AuditTrailService.log_task_activity(
            user=user,
            task=instance,
            action=AuditActionType.CREATE,
            description=f"Created task: {instance.title}",
            metadata={
                'task_title': instance.title,
                'priority': instance.priority,
                'assigned_to': instance.assigned_to.get_full_name() if instance.assigned_to else None,
                'due_time': None,
                'estimated_duration': instance.estimated_duration
            },
            request=request
        )
    else:
        old_values, new_values = AuditSignalMixin.get_model_changes(sender, instance, **kwargs)
        if old_values or new_values:
            # Determine the specific action based on status changes
            action = AuditActionType.UPDATE
            severity = AuditSeverity.LOW
            description = f"Updated task: {instance.title}"
            
            if new_values and 'status' in new_values:
                status = new_values['status']
                if status == 'in_progress':
                    action = AuditActionType.START
                    description = f"Started task: {instance.title}"
                    severity = AuditSeverity.MEDIUM
                elif status == 'completed':
                    action = AuditActionType.COMPLETE
                    description = f"Completed task: {instance.title}"
                    severity = AuditSeverity.MEDIUM
                elif status == 'paused':
                    action = AuditActionType.PAUSE
                    description = f"Paused task: {instance.title}"
            
            if new_values and 'progress_percentage' in new_values:
                action = AuditActionType.PROGRESS_UPDATE
                description = f"Updated progress for task: {instance.title} ({new_values['progress_percentage']}%)"
            
            if new_values and 'assigned_to' in new_values:
                action = AuditActionType.TASK_REASSIGN
                description = f"Reassigned task: {instance.title}"
                severity = AuditSeverity.MEDIUM
            
            AuditTrailService.log_task_activity(
                user=user,
                task=instance,
                action=action,
                description=description,
                old_values=old_values,
                new_values=new_values,
                metadata={
                    'task_title': instance.title,
                    'current_status': instance.status,
                    'progress': instance.progress_percentage
                },
                request=request
            )

@receiver(post_delete, sender=ShiftTask)
def log_shift_task_delete(sender, instance, **kwargs):
    """Log shift task deletion"""
    user = get_current_user()
    request = get_current_request()
    
    AuditTrailService.log_task_activity(
        user=user,
        task=instance,
        action=AuditActionType.DELETE,
        description=f"Deleted task: {instance.title}",
        severity=AuditSeverity.HIGH,
        metadata={
            'task_title': instance.title,
            'task_id': instance.id,
            'was_completed': instance.status == 'completed',
            'assigned_to': instance.assigned_to.get_full_name() if instance.assigned_to else None
        },
        request=request
    )

# Shift Swap Request Signals
@receiver(post_save, sender=ShiftSwapRequest)
def log_shift_swap_save(sender, instance, created, **kwargs):
    """Log shift swap request creation and updates"""
    user = get_current_user()
    request = get_current_request()
    
    if created:
        AuditTrailService.log_activity(
            user=user,
            action=AuditActionType.CREATE,
            description=f"Created shift swap request from {instance.requester.get_full_name()} to {instance.target_employee.get_full_name() if instance.target_employee else 'any employee'}",
            content_object=instance,
            severity=AuditSeverity.MEDIUM,
            metadata={
                'requester_id': instance.requester.id,
                'requester_name': instance.requester.get_full_name(),
                'target_employee_id': instance.target_employee.id if instance.target_employee else None,
                'target_employee_name': instance.target_employee.get_full_name() if instance.target_employee else None,
                'shift_date': instance.shift.shift_date.isoformat() if instance.shift else None,
                'reason': instance.reason
            },
            request=request
        )
    else:
        old_values, new_values = AuditSignalMixin.get_model_changes(sender, instance, **kwargs)
        if old_values or new_values:
            # Check for status changes
            action = AuditActionType.UPDATE
            severity = AuditSeverity.LOW
            description = f"Updated shift swap request"
            
            if new_values and 'status' in new_values:
                status = new_values['status']
                if status == 'approved':
                    action = AuditActionType.APPROVE
                    description = f"Approved shift swap request"
                    severity = AuditSeverity.HIGH
                elif status == 'rejected':
                    action = AuditActionType.REJECT
                    description = f"Rejected shift swap request"
                    severity = AuditSeverity.MEDIUM
                elif status == 'completed':
                    action = AuditActionType.SHIFT_SWAP
                    description = f"Completed shift swap between {instance.requester.get_full_name()} and {instance.target_employee.get_full_name() if instance.target_employee else 'another employee'}"
                    severity = AuditSeverity.HIGH
            
            AuditTrailService.log_activity(
                user=user,
                action=action,
                description=description,
                content_object=instance,
                old_values=old_values,
                new_values=new_values,
                severity=severity,
                metadata={
                    'requester_name': instance.requester.get_full_name(),
                    'current_status': instance.status,
                    'approved_by': instance.approved_by.get_full_name() if instance.approved_by else None
                },
                request=request
            )

# Custom signal for bulk operations
from django.dispatch import Signal

bulk_operation_signal = Signal()

@receiver(bulk_operation_signal)
def log_bulk_operation(sender, **kwargs):
    """Log bulk operations"""
    user = get_current_user()
    request = get_current_request()
    
    operation_type = kwargs.get('operation_type', 'unknown')
    affected_count = kwargs.get('affected_count', 0)
    model_name = kwargs.get('model_name', 'unknown')
    description = kwargs.get('description', f'Bulk {operation_type} operation')
    
    AuditTrailService.log_activity(
        user=user,
        action=AuditActionType.BULK_UPDATE,
        description=description,
        severity=AuditSeverity.HIGH if affected_count > 10 else AuditSeverity.MEDIUM,
        metadata={
            'operation_type': operation_type,
            'affected_count': affected_count,
            'model_name': model_name,
            'bulk_operation': True
        },
        request=request
    )