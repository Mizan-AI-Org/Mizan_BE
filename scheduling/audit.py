"""
Enhanced Audit Trail System for Scheduling and Task Management
Provides comprehensive logging and tracking of all activities
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from django.contrib.auth import get_user_model
from django.db import models
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.fields import GenericForeignKey
from django.utils import timezone as django_timezone
from enum import Enum

User = get_user_model()

def serialize_for_audit(data):
    """Serialize data for audit logging, handling UUIDs and other non-JSON types"""
    if data is None:
        return None
    
    def default_serializer(obj):
        if isinstance(obj, uuid.UUID):
            return str(obj)
        elif hasattr(obj, 'isoformat'):  # datetime objects
            return obj.isoformat()
        elif hasattr(obj, '__dict__'):  # model instances
            return str(obj)
        return str(obj)
    
    try:
        # Convert to JSON and back to ensure serialization works
        return json.loads(json.dumps(data, default=default_serializer))
    except (TypeError, ValueError):
        # If serialization fails, convert to string representation
        return str(data)

class AuditActionType(models.TextChoices):
    """Types of audit actions"""
    CREATE = 'CREATE', 'Create'
    UPDATE = 'UPDATE', 'Update'
    DELETE = 'DELETE', 'Delete'
    VIEW = 'VIEW', 'View'
    ASSIGN = 'ASSIGN', 'Assign'
    UNASSIGN = 'UNASSIGN', 'Unassign'
    START = 'START', 'Start'
    COMPLETE = 'COMPLETE', 'Complete'
    PAUSE = 'PAUSE', 'Pause'
    RESUME = 'RESUME', 'Resume'
    APPROVE = 'APPROVE', 'Approve'
    REJECT = 'REJECT', 'Reject'
    EXPORT = 'EXPORT', 'Export'
    IMPORT = 'IMPORT', 'Import'
    LOGIN = 'LOGIN', 'Login'
    LOGOUT = 'LOGOUT', 'Logout'
    BULK_UPDATE = 'BULK_UPDATE', 'Bulk Update'
    TEMPLATE_APPLY = 'TEMPLATE_APPLY', 'Template Apply'
    SCHEDULE_PUBLISH = 'SCHEDULE_PUBLISH', 'Schedule Publish'
    SHIFT_SWAP = 'SHIFT_SWAP', 'Shift Swap'
    TASK_REASSIGN = 'TASK_REASSIGN', 'Task Reassign'
    PROGRESS_UPDATE = 'PROGRESS_UPDATE', 'Progress Update'
    CHECKPOINT_ADD = 'CHECKPOINT_ADD', 'Checkpoint Add'
    PHOTO_UPLOAD = 'PHOTO_UPLOAD', 'Photo Upload'

class AuditSeverity(models.TextChoices):
    """Severity levels for audit events"""
    LOW = 'LOW', 'Low'
    MEDIUM = 'MEDIUM', 'Medium'
    HIGH = 'HIGH', 'High'
    CRITICAL = 'CRITICAL', 'Critical'

class AuditLog(models.Model):
    """Main audit log model for tracking all system activities"""
    
    # Basic audit information
    timestamp = models.DateTimeField(default=django_timezone.now, db_index=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=50, choices=AuditActionType.choices, db_index=True)
    severity = models.CharField(max_length=20, choices=AuditSeverity.choices, default=AuditSeverity.LOW)
    
    # Object being audited (generic foreign key)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True, blank=True)
    object_id = models.CharField(max_length=255, null=True, blank=True)  # Changed to CharField to support UUIDs
    content_object = GenericForeignKey('content_type', 'object_id')
    
    # Audit details
    description = models.TextField()
    old_values = models.JSONField(null=True, blank=True)
    new_values = models.JSONField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    
    # Request information
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    session_key = models.CharField(max_length=40, null=True, blank=True)
    
    # Restaurant context
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, null=True, blank=True)
    
    class Meta:
        db_table = 'audit_log'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['timestamp', 'restaurant']),
            models.Index(fields=['user', 'action']),
            models.Index(fields=['content_type', 'object_id']),
            models.Index(fields=['action', 'severity']),
        ]
    
    def __str__(self):
        return f"{self.timestamp} - {self.user} - {self.action} - {self.description[:50]}"

class AuditTrailService:
    """Service class for managing audit trails"""
    
    @staticmethod
    def log_activity(
        user: Optional[User],
        action: str,
        description: str,
        content_object: Optional[models.Model] = None,
        old_values: Optional[Dict[str, Any]] = None,
        new_values: Optional[Dict[str, Any]] = None,
        severity: str = AuditSeverity.LOW,
        metadata: Optional[Dict[str, Any]] = None,
        request=None
    ) -> AuditLog:
        """
        Log an audit activity
        
        Args:
            user: User performing the action
            action: Type of action (from AuditActionType)
            description: Human-readable description
            content_object: Object being acted upon
            old_values: Previous values (for updates)
            new_values: New values (for updates)
            severity: Severity level
            metadata: Additional metadata
            request: HTTP request object for IP/user agent
        
        Returns:
            AuditLog instance
        """
        audit_data = {
            'user': user,
            'action': action,
            'description': description,
            'severity': severity,
            'old_values': serialize_for_audit(old_values),
            'new_values': serialize_for_audit(new_values),
            'metadata': serialize_for_audit(metadata or {}),
        }
        
        # Set content object if provided
        if content_object:
            audit_data['content_object'] = content_object
        
        # Extract request information
        if request:
            audit_data.update({
                'ip_address': AuditTrailService._get_client_ip(request),
                'user_agent': request.META.get('HTTP_USER_AGENT', ''),
                'session_key': request.session.session_key,
            })
        
        # Set restaurant context
        if user and hasattr(user, 'restaurant'):
            audit_data['restaurant'] = user.restaurant
        elif content_object and hasattr(content_object, 'restaurant'):
            audit_data['restaurant'] = content_object.restaurant
        
        return AuditLog.objects.create(**audit_data)
    
    @staticmethod
    def log_task_activity(
        user: User,
        task,
        action: str,
        description: str,
        old_values: Optional[Dict] = None,
        new_values: Optional[Dict] = None,
        metadata: Optional[Dict] = None,
        request=None
    ):
        """Log task-specific activities"""
        severity = AuditSeverity.MEDIUM if action in [
            AuditActionType.COMPLETE, AuditActionType.ASSIGN, AuditActionType.TASK_REASSIGN
        ] else AuditSeverity.LOW
        
        return AuditTrailService.log_activity(
            user=user,
            action=action,
            description=description,
            content_object=task,
            old_values=old_values,
            new_values=new_values,
            severity=severity,
            metadata=metadata,
            request=request
        )
    
    @staticmethod
    def log_schedule_activity(
        user: User,
        schedule,
        action: str,
        description: str,
        old_values: Optional[Dict] = None,
        new_values: Optional[Dict] = None,
        metadata: Optional[Dict] = None,
        request=None
    ):
        """Log schedule-specific activities"""
        severity = AuditSeverity.HIGH if action in [
            AuditActionType.SCHEDULE_PUBLISH, AuditActionType.DELETE
        ] else AuditSeverity.MEDIUM
        
        return AuditTrailService.log_activity(
            user=user,
            action=action,
            description=description,
            content_object=schedule,
            old_values=old_values,
            new_values=new_values,
            severity=severity,
            metadata=metadata,
            request=request
        )
    
    @staticmethod
    def log_shift_activity(
        user: User,
        shift,
        action: str,
        description: str,
        old_values: Optional[Dict] = None,
        new_values: Optional[Dict] = None,
        metadata: Optional[Dict] = None,
        request=None
    ):
        """Log shift-specific activities"""
        severity = AuditSeverity.MEDIUM if action in [
            AuditActionType.SHIFT_SWAP, AuditActionType.ASSIGN, AuditActionType.UNASSIGN
        ] else AuditSeverity.LOW
        
        return AuditTrailService.log_activity(
            user=user,
            action=action,
            description=description,
            content_object=shift,
            old_values=old_values,
            new_values=new_values,
            severity=severity,
            metadata=metadata,
            request=request
        )
    
    @staticmethod
    def log_user_activity(
        user: User,
        action: str,
        description: str,
        target_user=None,
        metadata: Optional[Dict] = None,
        request=None
    ):
        """Log user-related activities"""
        severity = AuditSeverity.HIGH if action in [
            AuditActionType.LOGIN, AuditActionType.LOGOUT
        ] else AuditSeverity.MEDIUM
        
        return AuditTrailService.log_activity(
            user=user,
            action=action,
            description=description,
            content_object=target_user,
            severity=severity,
            metadata=metadata,
            request=request
        )
    
    @staticmethod
    def get_audit_trail(
        restaurant,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        user: Optional[User] = None,
        action: Optional[str] = None,
        content_type: Optional[ContentType] = None,
        severity: Optional[str] = None,
        limit: int = 100
    ) -> List[AuditLog]:
        """
        Retrieve audit trail with filters
        
        Args:
            restaurant: Restaurant to filter by
            start_date: Start date for filtering
            end_date: End date for filtering
            user: User to filter by
            action: Action type to filter by
            content_type: Content type to filter by
            severity: Severity level to filter by
            limit: Maximum number of records to return
        
        Returns:
            List of AuditLog instances
        """
        queryset = AuditLog.objects.filter(restaurant=restaurant)
        
        if start_date:
            queryset = queryset.filter(timestamp__gte=start_date)
        if end_date:
            queryset = queryset.filter(timestamp__lte=end_date)
        if user:
            queryset = queryset.filter(user=user)
        if action:
            queryset = queryset.filter(action=action)
        if content_type:
            queryset = queryset.filter(content_type=content_type)
        if severity:
            queryset = queryset.filter(severity=severity)
        
        return queryset.select_related('user', 'content_type')[:limit]
    
    @staticmethod
    def get_object_audit_trail(content_object, limit: int = 50) -> List[AuditLog]:
        """Get audit trail for a specific object"""
        content_type = ContentType.objects.get_for_model(content_object)
        return AuditLog.objects.filter(
            content_type=content_type,
            object_id=content_object.pk
        ).select_related('user')[:limit]
    
    @staticmethod
    def get_user_activity_summary(
        user: User,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Get activity summary for a user"""
        queryset = AuditLog.objects.filter(user=user)
        
        if start_date:
            queryset = queryset.filter(timestamp__gte=start_date)
        if end_date:
            queryset = queryset.filter(timestamp__lte=end_date)
        
        # Count activities by action type
        action_counts = {}
        for action_choice in AuditActionType.choices:
            action = action_choice[0]
            count = queryset.filter(action=action).count()
            if count > 0:
                action_counts[action] = count
        
        # Count activities by severity
        severity_counts = {}
        for severity_choice in AuditSeverity.choices:
            severity = severity_choice[0]
            count = queryset.filter(severity=severity).count()
            if count > 0:
                severity_counts[severity] = count
        
        return {
            'total_activities': queryset.count(),
            'action_counts': action_counts,
            'severity_counts': severity_counts,
            'first_activity': queryset.order_by('timestamp').first(),
            'last_activity': queryset.order_by('-timestamp').first(),
        }
    
    @staticmethod
    def export_audit_trail(
        restaurant,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        format: str = 'json'
    ) -> str:
        """Export audit trail data"""
        audit_logs = AuditTrailService.get_audit_trail(
            restaurant=restaurant,
            start_date=start_date,
            end_date=end_date,
            limit=10000  # Large limit for export
        )
        
        if format == 'json':
            data = []
            for log in audit_logs:
                data.append({
                    'timestamp': log.timestamp.isoformat(),
                    'user': f"{log.user.first_name} {log.user.last_name}" if log.user else None,
                    'action': log.action,
                    'severity': log.severity,
                    'description': log.description,
                    'object_type': log.content_type.model if log.content_type else None,
                    'object_id': log.object_id,
                    'old_values': log.old_values,
                    'new_values': log.new_values,
                    'metadata': log.metadata,
                    'ip_address': log.ip_address,
                })
            return json.dumps(data, indent=2)
        
        # Add other export formats as needed (CSV, XML, etc.)
        raise ValueError(f"Unsupported export format: {format}")
    
    @staticmethod
    def _get_client_ip(request) -> Optional[str]:
        """Extract client IP address from request"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip

class AuditMixin:
    """Mixin to add audit logging to model operations"""
    
    def save(self, *args, **kwargs):
        """Override save to log create/update operations"""
        is_new = self.pk is None
        old_values = None
        
        if not is_new:
            # Get old values for comparison
            try:
                old_instance = self.__class__.objects.get(pk=self.pk)
                old_values = self._get_audit_fields(old_instance)
            except self.__class__.DoesNotExist:
                pass
        
        # Save the instance
        super().save(*args, **kwargs)
        
        # Log the activity
        user = getattr(self, '_audit_user', None)
        request = getattr(self, '_audit_request', None)
        
        if is_new:
            AuditTrailService.log_activity(
                user=user,
                action=AuditActionType.CREATE,
                description=f"Created {self.__class__.__name__}: {str(self)}",
                content_object=self,
                new_values=self._get_audit_fields(self),
                request=request
            )
        else:
            new_values = self._get_audit_fields(self)
            if old_values != new_values:
                AuditTrailService.log_activity(
                    user=user,
                    action=AuditActionType.UPDATE,
                    description=f"Updated {self.__class__.__name__}: {str(self)}",
                    content_object=self,
                    old_values=old_values,
                    new_values=new_values,
                    request=request
                )
    
    def delete(self, *args, **kwargs):
        """Override delete to log deletion"""
        user = getattr(self, '_audit_user', None)
        request = getattr(self, '_audit_request', None)
        
        # Log before deletion
        AuditTrailService.log_activity(
            user=user,
            action=AuditActionType.DELETE,
            description=f"Deleted {self.__class__.__name__}: {str(self)}",
            content_object=self,
            old_values=self._get_audit_fields(self),
            severity=AuditSeverity.HIGH,
            request=request
        )
        
        super().delete(*args, **kwargs)
    
    def _get_audit_fields(self, instance) -> Dict[str, Any]:
        """Get fields to include in audit log"""
        # Override in subclasses to specify which fields to audit
        excluded_fields = ['id', 'created_at', 'updated_at', 'password']
        
        fields = {}
        for field in instance._meta.fields:
            if field.name not in excluded_fields:
                value = getattr(instance, field.name)
                if hasattr(value, 'pk'):
                    # For foreign keys, store the ID and string representation
                    fields[field.name] = {'id': value.pk, 'str': str(value)}
                else:
                    fields[field.name] = value
        
        return fields
    
    def set_audit_context(self, user: User, request=None):
        """Set audit context for this instance"""
        self._audit_user = user
        self._audit_request = request