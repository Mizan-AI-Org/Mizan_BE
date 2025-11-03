from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe
from .models import (
    Notification, 
    DeviceToken, 
    NotificationPreference, 
    NotificationTemplate, 
    NotificationLog
)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'recipient_name', 'sender_name', 'title', 'notification_type', 
        'priority', 'is_read_display', 'channels_sent_display', 'created_at'
    ]
    list_filter = [
        'notification_type', 'priority', 'created_at', 'read_at', 
        'channels_sent', 'delivery_status'
    ]
    search_fields = ['recipient__email', 'sender__email', 'title', 'message']
    readonly_fields = ['id', 'created_at', 'read_at', 'channels_sent', 'delivery_status']
    date_hierarchy = 'created_at'
    ordering = ['-created_at']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('recipient', 'sender', 'title', 'message')
        }),
        ('Classification', {
            'fields': ('notification_type', 'priority')
        }),
        ('Metadata', {
            'fields': ('data', 'related_shift_id', 'related_task_id', 'expires_at')
        }),
        ('Status', {
            'fields': ('read_at', 'channels_sent', 'delivery_status'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        })
    )
    
    def recipient_name(self, obj):
        return obj.recipient.get_full_name() or obj.recipient.email
    recipient_name.short_description = 'Recipient'
    
    def sender_name(self, obj):
        if obj.sender:
            return obj.sender.get_full_name() or obj.sender.email
        return 'System'
    sender_name.short_description = 'Sender'
    
    def is_read_display(self, obj):
        if obj.read_at:
            return format_html(
                '<span style="color: green;">✓ Read</span>'
            )
        return format_html(
            '<span style="color: red;">✗ Unread</span>'
        )
    is_read_display.short_description = 'Status'
    
    def channels_sent_display(self, obj):
        if obj.channels_sent:
            channels = obj.channels_sent
            colors = {
                'app': 'blue',
                'email': 'green', 
                'push': 'orange',
                'whatsapp': 'purple'
            }
            badges = []
            for channel in channels:
                color = colors.get(channel, 'gray')
                badges.append(f'<span style="background-color: {color}; color: white; padding: 2px 6px; border-radius: 3px; font-size: 11px; margin-right: 2px;">{channel.upper()}</span>')
            return mark_safe(''.join(badges))
        return '-'
    channels_sent_display.short_description = 'Channels'
    
    actions = ['mark_as_read', 'resend_notification']
    
    def mark_as_read(self, request, queryset):
        from django.utils import timezone
        count = queryset.filter(read_at__isnull=True).update(read_at=timezone.now())
        self.message_user(request, f'{count} notifications marked as read.')
    mark_as_read.short_description = 'Mark selected notifications as read'
    
    def resend_notification(self, request, queryset):
        from .services import notification_service
        count = 0
        for notification in queryset:
            try:
                # Resend using the notification service
                notification_service.send_custom_notification(
                    recipient=notification.recipient,
                    message=notification.message,
                    notification_type=notification.notification_type,
                    channels=['app']  # Default to app channel for resend
                )
                count += 1
            except Exception:
                pass
        self.message_user(request, f'{count} notifications resent successfully.')
    resend_notification.short_description = 'Resend selected notifications'


@admin.register(DeviceToken)
class DeviceTokenAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'user_name', 'device_type', 'device_name', 
        'is_active', 'last_used', 'created_at'
    ]
    list_filter = ['device_type', 'is_active', 'created_at', 'last_used']
    search_fields = ['user__email', 'device_name', 'token']
    readonly_fields = ['id', 'created_at', 'last_used']
    date_hierarchy = 'created_at'
    ordering = ['-last_used']
    
    def user_name(self, obj):
        return obj.user.get_full_name() or obj.user.email
    user_name.short_description = 'User'
    
    actions = ['deactivate_tokens', 'activate_tokens']
    
    def deactivate_tokens(self, request, queryset):
        count = queryset.update(is_active=False)
        self.message_user(request, f'{count} device tokens deactivated.')
    deactivate_tokens.short_description = 'Deactivate selected tokens'
    
    def activate_tokens(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(request, f'{count} device tokens activated.')
    activate_tokens.short_description = 'Activate selected tokens'


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = [
        'user_name', 'email_enabled', 'push_enabled', 'whatsapp_enabled',
        'shift_notifications', 'task_notifications', 'updated_at'
    ]
    list_filter = [
        'email_enabled', 'push_enabled', 'whatsapp_enabled',
        'shift_notifications', 'task_notifications', 'availability_notifications'
    ]
    search_fields = ['user__email', 'user__first_name', 'user__last_name']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('User', {
            'fields': ('user',)
        }),
        ('Channel Preferences', {
            'fields': ('email_enabled', 'push_enabled', 'whatsapp_enabled')
        }),
        ('Notification Types', {
            'fields': (
                'shift_notifications', 'task_notifications', 'availability_notifications',
                'compliance_notifications', 'emergency_notifications', 'announcement_notifications'
            )
        }),
        ('Quiet Hours', {
            'fields': ('quiet_hours_start', 'quiet_hours_end', 'timezone'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    
    def user_name(self, obj):
        return obj.user.get_full_name() or obj.user.email
    user_name.short_description = 'User'


@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'template_type', 'channel', 'is_active', 
        'created_by_name', 'created_at'
    ]
    list_filter = ['template_type', 'channel', 'is_active', 'created_at']
    search_fields = ['name', 'subject_template', 'body_template']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'template_type', 'channel', 'is_active')
        }),
        ('Templates', {
            'fields': ('subject_template', 'body_template')
        }),
        ('Metadata', {
            'fields': ('created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    
    def created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name() or obj.created_by.email
        return 'System'
    created_by_name.short_description = 'Created By'
    
    def save_model(self, request, obj, form, change):
        if not change:  # Only set created_by on creation
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'notification_title', 'recipient_name', 'channel', 
        'status', 'sent_at', 'delivered_at'
    ]
    list_filter = ['channel', 'status', 'sent_at', 'delivered_at']
    search_fields = [
        'notification__title', 'notification__recipient__email',
        'error_message'
    ]
    readonly_fields = [
        'id', 'notification', 'channel', 'status', 'error_message',
        'sent_at', 'delivered_at', 'response_data', 'external_id', 'attempt_count'
    ]
    date_hierarchy = 'sent_at'
    ordering = ['-sent_at']
    
    def notification_title(self, obj):
        return obj.notification.title
    notification_title.short_description = 'Notification'
    
    def recipient_name(self, obj):
        return obj.notification.recipient.get_full_name() or obj.notification.recipient.email
    recipient_name.short_description = 'Recipient'
    
    def has_add_permission(self, request):
        return False  # Logs should not be manually created
    
    def has_change_permission(self, request, obj=None):
        return False  # Logs should not be edited
    
    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser  # Only superusers can delete logs


# Custom admin site configuration
admin.site.site_header = "Mizan Notification Management"
admin.site.site_title = "Mizan Notifications"
admin.site.index_title = "Notification Administration"
