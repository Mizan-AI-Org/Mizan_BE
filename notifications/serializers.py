from rest_framework import serializers
from .models import Notification, DeviceToken, NotificationPreference, NotificationTemplate, NotificationLog


class NotificationSerializer(serializers.ModelSerializer):
    sender_name = serializers.CharField(source='sender.get_full_name', read_only=True)
    is_read = serializers.SerializerMethodField()
    time_ago = serializers.SerializerMethodField()
    
    class Meta:
        model = Notification
        fields = [
            'id', 'recipient', 'sender', 'sender_name', 'title', 'message', 
            'notification_type', 'priority', 'data', 'is_read', 'read_at', 
            'channels_sent', 'delivery_status', 'related_shift_id', 
            'related_task_id', 'expires_at', 'created_at', 'time_ago'
        ]
        read_only_fields = [
            'id', 'recipient', 'sender', 'sender_name', 'channels_sent', 
            'delivery_status', 'created_at', 'time_ago'
        ]
    
    def get_is_read(self, obj):
        return obj.read_at is not None
    
    def get_time_ago(self, obj):
        from django.utils import timezone
        from datetime import timedelta
        
        now = timezone.now()
        diff = now - obj.created_at
        
        if diff.days > 0:
            return f"{diff.days} day{'s' if diff.days > 1 else ''} ago"
        elif diff.seconds > 3600:
            hours = diff.seconds // 3600
            return f"{hours} hour{'s' if hours > 1 else ''} ago"
        elif diff.seconds > 60:
            minutes = diff.seconds // 60
            return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
        else:
            return "Just now"


class DeviceTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceToken
        fields = [
            'id', 'user', 'token', 'device_type', 'device_name', 
            'is_active', 'last_used', 'created_at'
        ]
        read_only_fields = ['id', 'user', 'created_at']


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = [
            'id', 'user', 'email_enabled', 'push_enabled', 'whatsapp_enabled',
            'shift_notifications', 'task_notifications', 'availability_notifications',
            'compliance_notifications', 'emergency_notifications', 'announcement_notifications',
            'quiet_hours_start', 'quiet_hours_end', 'timezone', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'user', 'created_at', 'updated_at']


class NotificationTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationTemplate
        fields = [
            'id', 'name', 'notification_type', 'channel', 'subject_template',
            'body_template', 'is_active', 'created_by', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at']


class NotificationLogSerializer(serializers.ModelSerializer):
    notification_title = serializers.CharField(source='notification.title', read_only=True)
    recipient_name = serializers.CharField(source='notification.recipient.get_full_name', read_only=True)
    
    class Meta:
        model = NotificationLog
        fields = [
            'id', 'notification', 'notification_title', 'recipient_name',
            'channel', 'status', 'error_message', 'sent_at', 'delivered_at',
            'read_at', 'metadata'
        ]
        read_only_fields = ['id', 'notification_title', 'recipient_name']


class BulkNotificationSerializer(serializers.Serializer):
    """Serializer for bulk notification operations"""
    action = serializers.ChoiceField(choices=['mark_read', 'delete'])
    notification_ids = serializers.ListField(
        child=serializers.IntegerField(),
        min_length=1
    )


class TestNotificationSerializer(serializers.Serializer):
    """Serializer for sending test notifications"""
    message = serializers.CharField(max_length=500, default="This is a test notification")
    channels = serializers.ListField(
        child=serializers.ChoiceField(choices=['app', 'email', 'push', 'whatsapp']),
        default=['app']
    )
