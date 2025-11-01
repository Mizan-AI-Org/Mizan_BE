"""Audit functionality for Django admin
"""
from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.http import HttpResponse
from django.db.models import Count, Q
from django.utils import timezone
from datetime import datetime, timedelta
import json
import csv

from .audit import AuditLog, AuditActionType, AuditSeverity, AuditTrailService

class AuditLogAdmin(admin.ModelAdmin):
    """Admin interface for AuditLog model"""
    
    list_display = [
        'timestamp',
        'user_display',
        'action_display',
        'severity_display',
        'description_short',
        'content_object_display',
        'ip_address',
        'restaurant_display'
    ]
    
    list_filter = [
        'action',
        'severity',
        'timestamp',
        'content_type',
        'restaurant',
        ('user', admin.RelatedOnlyFieldListFilter),
    ]
    
    search_fields = [
        'description',
        'user__username',
        'user__first_name',
        'user__last_name',
        'user__email',
        'ip_address',
        'user_agent',
    ]
    
    readonly_fields = [
        'timestamp',
        'user',
        'action',
        'severity',
        'content_type',
        'object_id',
        'content_object',
        'description',
        'old_values_display',
        'new_values_display',
        'metadata_display',
        'ip_address',
        'user_agent',
        'session_key',
        'restaurant'
    ]
    
    date_hierarchy = 'timestamp'
    
    ordering = ['-timestamp']
    
    list_per_page = 50
    
    actions = [
        'export_selected_csv',
        'export_selected_json',
        'mark_as_reviewed'
    ]
    
    def get_queryset(self, request):
        """Optimize queryset with select_related"""
        return super().get_queryset(request).select_related(
            'user',
            'content_type',
            'restaurant'
        )
    
    def user_display(self, obj):
        """Display user with link to user admin"""
        if obj.user:
            url = reverse('admin:auth_user_change', args=[obj.user.pk])
            return format_html(
                '<a href="{}">{}</a>',
                url,
                obj.user.get_full_name() or obj.user.username
            )
        return "System"
    user_display.short_description = "User"
    user_display.admin_order_field = 'user__username'
    
    def action_display(self, obj):
        """Display action with color coding"""
        colors = {
            AuditActionType.CREATE: '#28a745',
            AuditActionType.UPDATE: '#007bff',
            AuditActionType.DELETE: '#dc3545',
            AuditActionType.LOGIN: '#17a2b8',
            AuditActionType.LOGOUT: '#6c757d',
            AuditActionType.APPROVE: '#28a745',
            AuditActionType.REJECT: '#dc3545',
            AuditActionType.START: '#ffc107',
            AuditActionType.COMPLETE: '#28a745',
        }
        color = colors.get(obj.action, '#6c757d')
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_action_display()
        )
    action_display.short_description = "Action"
    action_display.admin_order_field = 'action'
    
    def severity_display(self, obj):
        """Display severity with color coding"""
        colors = {
            AuditSeverity.LOW: '#28a745',
            AuditSeverity.MEDIUM: '#ffc107',
            AuditSeverity.HIGH: '#fd7e14',
            AuditSeverity.CRITICAL: '#dc3545',
        }
        color = colors.get(obj.severity, '#6c757d')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 6px; border-radius: 3px; font-size: 11px;">{}</span>',
            color,
            obj.get_severity_display()
        )
    severity_display.short_description = "Severity"
    severity_display.admin_order_field = 'severity'
    
    def description_short(self, obj):
        """Display truncated description"""
        if len(obj.description) > 80:
            return obj.description[:80] + "..."
        return obj.description
    description_short.short_description = "Description"
    
    def content_object_display(self, obj):
        """Display content object with link if possible"""
        if obj.content_object:
            try:
                # Try to get admin URL for the object
                content_type = obj.content_type
                app_label = content_type.app_label
                model_name = content_type.model
                
                url = reverse(
                    f'admin:{app_label}_{model_name}_change',
                    args=[obj.object_id]
                )
                return format_html(
                    '<a href="{}">{}</a>',
                    url,
                    str(obj.content_object)
                )
            except:
                return str(obj.content_object)
        elif obj.content_type:
            return f"{obj.content_type.model} (deleted)"
        return "-"
    content_object_display.short_description = "Object"
    
    def restaurant_display(self, obj):
        """Display restaurant with link"""
        if obj.restaurant:
            try:
                url = reverse('admin:accounts_restaurant_change', args=[obj.restaurant.pk])
                return format_html(
                    '<a href="{}">{}</a>',
                    url,
                    obj.restaurant.name
                )
            except:
                return obj.restaurant.name
        return "-"
    restaurant_display.short_description = "Restaurant"
    
    def old_values_display(self, obj):
        """Display old values as formatted JSON"""
        if obj.old_values:
            return format_html(
                '<pre style="max-height: 200px; overflow-y: auto; font-size: 11px;">{}</pre>',
                json.dumps(obj.old_values, indent=2)
            )
        return "-"
    old_values_display.short_description = "Old Values"
    
    def new_values_display(self, obj):
        """Display new values as formatted JSON"""
        if obj.new_values:
            return format_html(
                '<pre style="max-height: 200px; overflow-y: auto; font-size: 11px;">{}</pre>',
                json.dumps(obj.new_values, indent=2)
            )
        return "-"
    new_values_display.short_description = "New Values"
    
    def metadata_display(self, obj):
        """Display metadata as formatted JSON"""
        if obj.metadata:
            return format_html(
                '<pre style="max-height: 200px; overflow-y: auto; font-size: 11px;">{}</pre>',
                json.dumps(obj.metadata, indent=2)
            )
        return "-"
    metadata_display.short_description = "Metadata"
    
    def has_add_permission(self, request):
        """Disable adding audit logs through admin"""
        return False
    
    def has_change_permission(self, request, obj=None):
        """Disable changing audit logs through admin"""
        return False
    
    def has_delete_permission(self, request, obj=None):
        """Only allow superusers to delete audit logs"""
        return request.user.is_superuser
    
    def export_selected_csv(self, request, queryset):
        """Export selected audit logs as CSV"""
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="audit_logs_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'
        
        writer = csv.writer(response)
        writer.writerow([
            'Timestamp',
            'User',
            'Action',
            'Severity',
            'Description',
            'Content Type',
            'Object ID',
            'IP Address',
            'Restaurant'
        ])
        
        for log in queryset:
            writer.writerow([
                log.timestamp.isoformat(),
                log.user.get_full_name() if log.user else 'System',
                log.get_action_display(),
                log.get_severity_display(),
                log.description,
                str(log.content_type) if log.content_type else '',
                log.object_id or '',
                log.ip_address or '',
                log.restaurant.name if log.restaurant else ''
            ])
        
        return response
    export_selected_csv.short_description = "Export selected as CSV"
    
    def export_selected_json(self, request, queryset):
        """Export selected audit logs as JSON"""
        response = HttpResponse(content_type='application/json')
        response['Content-Disposition'] = f'attachment; filename="audit_logs_{timezone.now().strftime("%Y%m%d_%H%M%S")}.json"'
        
        logs = []
        for log in queryset:
            logs.append({
                'timestamp': log.timestamp.isoformat(),
                'user': {
                    'id': log.user.id if log.user else None,
                    'username': log.user.username if log.user else None,
                    'full_name': log.user.get_full_name() if log.user else None
                },
                'action': log.action,
                'action_display': log.get_action_display(),
                'severity': log.severity,
                'severity_display': log.get_severity_display(),
                'description': log.description,
                'content_type': str(log.content_type) if log.content_type else None,
                'object_id': log.object_id,
                'old_values': log.old_values,
                'new_values': log.new_values,
                'metadata': log.metadata,
                'ip_address': log.ip_address,
                'user_agent': log.user_agent,
                'restaurant': {
                    'id': log.restaurant.id if log.restaurant else None,
                    'name': log.restaurant.name if log.restaurant else None
                }
            })
        
        response.write(json.dumps(logs, indent=2))
        return response
    export_selected_json.short_description = "Export selected as JSON"
    
    def mark_as_reviewed(self, request, queryset):
        """Mark selected logs as reviewed (add metadata)"""
        count = 0
        for log in queryset:
            if not log.metadata:
                log.metadata = {}
            log.metadata['reviewed'] = True
            log.metadata['reviewed_by'] = request.user.username
            log.metadata['reviewed_at'] = timezone.now().isoformat()
            log.save()
            count += 1
        
        self.message_user(request, f"Marked {count} audit logs as reviewed.")
    mark_as_reviewed.short_description = "Mark selected as reviewed"
    
    def changelist_view(self, request, extra_context=None):
        """Add custom context to changelist view"""
        extra_context = extra_context or {}
        
        # Add summary statistics
        queryset = self.get_queryset(request)
        
        # Recent activity (last 24 hours)
        recent_cutoff = timezone.now() - timedelta(hours=24)
        recent_logs = queryset.filter(timestamp__gte=recent_cutoff)
        
        # Statistics by action
        action_stats = queryset.values('action').annotate(
            count=Count('id')
        ).order_by('-count')[:10]
        
        # Statistics by severity
        severity_stats = queryset.values('severity').annotate(
            count=Count('id')
        ).order_by('-count')
        
        # Top users by activity
        user_stats = queryset.filter(user__isnull=False).values(
            'user__username',
            'user__first_name',
            'user__last_name'
        ).annotate(count=Count('id')).order_by('-count')[:10]
        
        extra_context.update({
            'total_logs': queryset.count(),
            'recent_logs_count': recent_logs.count(),
            'action_stats': action_stats,
            'severity_stats': severity_stats,
            'user_stats': user_stats,
        })
        
        return super().changelist_view(request, extra_context)


def add_audit_info_to_admin(admin_class):
    """
    Decorator to add audit information to admin classes
    """
    original_change_view = admin_class.change_view
    
    def change_view_with_audit(self, request, object_id, form_url='', extra_context=None):
        """Add audit trail to change view context"""
        extra_context = extra_context or {}
        
        # Get the model instance
        try:
            obj = self.get_object(request, object_id)
            if obj:
                # Get content type for this model
                content_type = ContentType.objects.get_for_model(obj)
                
                # Get audit logs for this object
                audit_logs = AuditLog.objects.filter(
                    content_type=content_type,
                    object_id=obj.pk
                ).order_by('-timestamp')[:20]  # Last 20 entries
                
                extra_context['audit_logs'] = audit_logs
        except:
            pass
        
        return original_change_view(self, request, object_id, form_url, extra_context)
    
    admin_class.change_view = change_view_with_audit
    return admin_class


# Register the admin
admin.site.register(AuditLog, AuditLogAdmin)

# Custom admin site for audit-only access
class AuditAdminSite(admin.AdminSite):
    """Custom admin site for audit log access only"""
    site_header = "Mizan Audit Trail"
    site_title = "Audit Trail"
    index_title = "Audit Trail Administration"
    
    def has_permission(self, request):
        """Allow access to users with audit permissions"""
        return (
            request.user.is_active and
            (request.user.is_staff or 
             request.user.has_perm('scheduling.view_auditlog'))
        )

# Create audit admin site instance
audit_admin_site = AuditAdminSite(name='audit_admin')
audit_admin_site.register(AuditLog, AuditLogAdmin)