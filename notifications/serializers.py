from rest_framework import serializers
from django.db import models
from django.utils import timezone
from datetime import datetime
import uuid

from .models import (
    Notification,
    DeviceToken,
    NotificationPreference,
    NotificationTemplate,
    NotificationLog,
    NotificationAttachment
)

# -----------------------------------------
# SAFE JSON SERIALIZATION (UUID + DATETIME)
# -----------------------------------------
import uuid
from datetime import datetime

def to_json_safe(value):
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [to_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: to_json_safe(v) for k, v in value.items()}
    return value


# -----------------------------------------
# SERIALIZERS
# -----------------------------------------

class NotificationSerializer(serializers.ModelSerializer):
    sender_name = serializers.CharField(source='sender.get_full_name', read_only=True)
    is_read = serializers.SerializerMethodField()
    time_ago = serializers.SerializerMethodField()
    attachments = serializers.SerializerMethodField()
    
    class Meta:
        model = Notification
        fields = [
            'id', 'recipient', 'sender', 'sender_name', 'title', 'message',
            'notification_type', 'priority', 'data', 'is_read', 'read_at',
            'channels_sent', 'delivery_status', 'related_shift_id',
            'related_task_id', 'expires_at', 'created_at', 'time_ago',
            'attachments'
        ]
        read_only_fields = [
            'id', 'recipient', 'sender', 'sender_name', 'channels_sent',
            'delivery_status', 'created_at', 'time_ago'
        ]
    
    def get_is_read(self, obj):
        return obj.read_at is not None
    
    def get_time_ago(self, obj):
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

    def get_attachments(self, obj):
        items = []
        for att in obj.attachments.all():
            try:
                url = att.file.url
            except Exception:
                url = ''
            try:
                file_name = getattr(att.file, 'name', '')
            except Exception:
                file_name = ''
            items.append({
                'name': att.original_name or file_name,
                'url': url,
                'content_type': att.content_type,
                'size': att.file_size,
                'uploaded_at': att.uploaded_at,
            })
        return items
    def to_representation(self, instance):
        rep = super().to_representation(instance)
        # Convert any UUID/datetime nested in the representation
        return to_json_safe(rep)



class NotificationAttachmentSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = NotificationAttachment
        fields = ['id', 'original_name', 'content_type', 'file_size', 'uploaded_at', 'url']
        read_only_fields = ['id', 'uploaded_at', 'url']

    def get_url(self, obj):
        try:
            return obj.file.url
        except Exception:
            return ''


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
            'quiet_hours_start', 'quiet_hours_end', 'created_at', 'updated_at'
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
    action = serializers.ChoiceField(choices=['mark_read', 'delete'])
    notification_ids = serializers.ListField(
        child=serializers.IntegerField(),
        min_length=1
    )


class TestNotificationSerializer(serializers.Serializer):
    message = serializers.CharField(max_length=500, default="This is a test notification")
    channels = serializers.ListField(
        child=serializers.ChoiceField(choices=['app', 'email', 'push', 'whatsapp']),
        default=['app']
    )


# ---------------------------------------------------
# ANNOUNCEMENT SERIALIZER (WITH FIXED UUID HANDLING)
# ---------------------------------------------------

class AnnouncementCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    message = serializers.CharField(max_length=2000)
    priority = serializers.ChoiceField(
        choices=['LOW', 'MEDIUM', 'HIGH', 'URGENT'],
        default='MEDIUM'
    )
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
    schedule_for = serializers.DateTimeField(required=False, allow_null=True)

    tags = serializers.ListField(
        child=serializers.CharField(max_length=50),
        required=False,
        allow_empty=True
    )

    recipients_staff_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=True
    )
    recipients_departments = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False,
        allow_empty=True
    )
    recipients_roles = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False,
        allow_empty=True
    )
    recipients_shift_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=True
    )
    
    def validate_expires_at(self, value):
        if value and value <= timezone.now():
            raise serializers.ValidationError("Expiration date must be in the future")
        return value
    
    def validate_schedule_for(self, value):
        if value and value <= timezone.now():
            raise serializers.ValidationError("Schedule date must be in the future")
        return value
    
    def create_notifications(self, sender):
        from accounts.models import CustomUser, StaffProfile
        from scheduling.models import AssignedShift

        staff_ids = self.validated_data.get('recipients_staff_ids') or []
        departments = self.validated_data.get('recipients_departments') or []
        roles = self.validated_data.get('recipients_roles') or []
        shift_ids = self.validated_data.get('recipients_shift_ids') or []

        staff_qs = CustomUser.objects.filter(
            restaurant=sender.restaurant,
            is_active=True
        )

        targeted = False
        filters = models.Q()

        if staff_ids:
            targeted = True
            filters |= models.Q(id__in=staff_ids)

        if departments:
            targeted = True
            filters |= models.Q(profile__department__in=departments)

        if roles:
            targeted = True
            filters |= models.Q(profile__position__in=roles)

        if shift_ids:
            targeted = True
            target_staff = AssignedShift.objects.filter(
                id__in=shift_ids,
                schedule__restaurant=sender.restaurant
            ).values_list('staff_id', flat=True)

            filters |= models.Q(id__in=list(target_staff))

        if targeted:
            staff_qs = staff_qs.filter(filters)

        staff_qs = staff_qs.exclude(id=sender.id)

        notifications = []

        for staff in staff_qs:
            raw_data = {
                'announcement': True,
                'scheduled_for': (
                    self.validated_data.get('schedule_for').isoformat()
                    if self.validated_data.get('schedule_for') else None
                ),
                'targeted': targeted,
                'tags': self.validated_data.get('tags', []),
                'targeting': {
                    'staff_ids': staff_ids,
                    'departments': departments,
                    'roles': roles,
                    'shift_ids': shift_ids,
                }
            }

            safe_data = to_json_safe(raw_data)

            notification = Notification.objects.create(
                recipient=staff,
                sender=sender,
                title=self.validated_data['title'],
                message=self.validated_data['message'],
                notification_type='ANNOUNCEMENT',
                priority=self.validated_data['priority'],
                expires_at=self.validated_data.get('expires_at'),
                data=safe_data
            )

            notifications.append(notification)

        return notifications
