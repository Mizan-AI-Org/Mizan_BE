from rest_framework import serializers
from .models import (
    Schedule,
    StaffProfile,
    StaffDocument,
    ScheduleChange,
    ScheduleNotification,
    StaffAvailability,
    PerformanceMetric,
    StaffRequest,
    StaffRequestComment,
)
from .models_task import StandardOperatingProcedure, SafetyChecklist, ScheduleTask, SafetyConcernReport, SafetyRecognition
from accounts.serializers import CustomUserSerializer, RestaurantSerializer
import decimal
from accounts.models import CustomUser, Restaurant
from typing import Tuple

class StaffProfileSerializer(serializers.ModelSerializer):
    user_details = CustomUserSerializer(source='user', read_only=True)
    
    class Meta:
        model = StaffProfile
        fields = '__all__'
        read_only_fields = ('user',)

class StaffDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = StaffDocument
        fields = '__all__'
        read_only_fields = ('uploaded_at', 'staff')

class ScheduleSerializer(serializers.ModelSerializer):
    staff_details = CustomUserSerializer(source='staff', read_only=True)
    restaurant_details = RestaurantSerializer(source='restaurant', read_only=True)
    
    class Meta:
        model = Schedule
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at', 'backup_data')
        
    def validate(self, data):
        """
        Validate schedule data to ensure start_time is before end_time
        and recurring schedules have a pattern
        """
        if 'start_time' in data and 'end_time' in data:
            if data['start_time'] >= data['end_time']:
                raise serializers.ValidationError("End time must be after start time")
                
        if data.get('is_recurring', False) and not data.get('recurrence_pattern'):
            raise serializers.ValidationError("Recurrence pattern is required for recurring schedules")
            
        return data
        
    def create(self, validated_data):
        # Set the created_by field to the current user
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            validated_data['created_by'] = request.user
            validated_data['last_modified_by'] = request.user
            
        return super().create(validated_data)
        
    def update(self, instance, validated_data):
        # Set the last_modified_by field to the current user
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            validated_data['last_modified_by'] = request.user
            
        return super().update(instance, validated_data)

class ScheduleChangeSerializer(serializers.ModelSerializer):
    changed_by_details = CustomUserSerializer(source='changed_by', read_only=True)
    
    class Meta:
        model = ScheduleChange
        fields = '__all__'
        read_only_fields = ('id', 'timestamp')

class ScheduleNotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScheduleNotification
        fields = '__all__'
        read_only_fields = ('id', 'created_at')

class StaffAvailabilitySerializer(serializers.ModelSerializer):
    day_name = serializers.SerializerMethodField()
    
    class Meta:
        model = StaffAvailability
        fields = '__all__'
        
    def get_day_name(self, obj):
        days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        return days[obj.day_of_week]

class PerformanceMetricSerializer(serializers.ModelSerializer):
    staff_details = CustomUserSerializer(source='staff', read_only=True)
    
    class Meta:
        model = PerformanceMetric
        fields = '__all__'

# New serializers for task management models
class StandardOperatingProcedureSerializer(serializers.ModelSerializer):
    class Meta:
        model = StandardOperatingProcedure
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at')

class SafetyChecklistSerializer(serializers.ModelSerializer):
    class Meta:
        model = SafetyChecklist
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at')

class ScheduleTaskSerializer(serializers.ModelSerializer):
    sop_details = StandardOperatingProcedureSerializer(source='sop', read_only=True)
    checklist_details = SafetyChecklistSerializer(source='safety_checklist', read_only=True)
    assigned_to_details = CustomUserSerializer(source='assigned_to', read_only=True)
    
    class Meta:
        model = ScheduleTask
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at', 'completion_time')

class SafetyConcernReportSerializer(serializers.ModelSerializer):
    reporter_details = CustomUserSerializer(source='reporter', read_only=True, required=False)
    assigned_to_details = CustomUserSerializer(source='assigned_to', read_only=True)

    class Meta:
        model = SafetyConcernReport
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at', 'restaurant', 'reporter')
        
    def create(self, validated_data):
        # Handle anonymous reports
        request = self.context.get('request')
        if not validated_data.get('is_anonymous') and request and hasattr(request, 'user'):
            validated_data['reporter'] = request.user
        elif validated_data.get('is_anonymous'):
            validated_data['reporter'] = None
            
        return super().create(validated_data)

class SafetyRecognitionSerializer(serializers.ModelSerializer):
    staff_details = CustomUserSerializer(source='staff', read_only=True)
    recognized_by_details = CustomUserSerializer(source='recognized_by', read_only=True)
    
    class Meta:
        model = SafetyRecognition
        fields = '__all__'
        read_only_fields = ('created_at',)


class StaffRequestCommentSerializer(serializers.ModelSerializer):
    author_details = CustomUserSerializer(source='author', read_only=True)

    class Meta:
        model = StaffRequestComment
        fields = ['id', 'kind', 'body', 'metadata', 'created_at', 'author', 'author_details']
        read_only_fields = ['id', 'created_at', 'author', 'author_details']


def _phone_from_activation_email(email: str | None) -> str:
    """Extract +212… from wa_212…@mizan.activation-style accounts."""
    raw = (email or "").strip().lower()
    if not raw or "@" not in raw:
        return ""
    local = raw.split("@", 1)[0]
    if local.startswith("wa_"):
        digits = "".join(c for c in local[3:] if c.isdigit())
        if len(digits) >= 8:
            return f"+{digits}"
    return ""


def _format_phone_label(phone: str | None) -> str:
    raw = (phone or "").strip()
    if not raw:
        return ""
    digits = "".join(c for c in raw if c.isdigit())
    if raw.startswith("+") and digits:
        return f"+{digits}"
    if digits:
        return f"+{digits}" if len(digits) >= 10 else digits
    return raw


def resolve_staff_request_sender(obj) -> Tuple[str, str]:
    """
    Return (display_name, phone) for a StaffRequest.

    Never returns the useless placeholder \"Staff\" when any identity signal
    exists (linked user name, stored staff_name, WhatsApp profile in metadata,
    phone on the row / user, or wa_* activation email).
    """
    phone = (getattr(obj, "staff_phone", None) or "").strip()
    name = (getattr(obj, "staff_name", None) or "").strip()
    if name.lower() in ("staff", "a staff member", "unknown", "unknown sender"):
        name = ""

    staff = getattr(obj, "staff", None) if getattr(obj, "staff_id", None) else None
    if staff is not None:
        try:
            full = staff.get_full_name() or ""
        except Exception:
            first = getattr(staff, "first_name", "") or ""
            last = getattr(staff, "last_name", "") or ""
            full = f"{first} {last}".strip()
        if full:
            name = name or full
        phone = phone or (getattr(staff, "phone", None) or "").strip()
        if not phone:
            phone = _phone_from_activation_email(getattr(staff, "email", None))
        if not name:
            email = (getattr(staff, "email", None) or "").strip()
            if email and not email.lower().startswith("wa_"):
                local = email.split("@", 1)[0].replace(".", " ").replace("_", " ").strip()
                if local:
                    name = local.title()

    md = getattr(obj, "metadata", None) or {}
    if isinstance(md, dict) and not name:
        for key in (
            "sender_name",
            "push_name",
            "pushName",
            "profile_name",
            "profileName",
            "wa_profile_name",
            "whatsapp_name",
        ):
            val = (md.get(key) or "").strip() if isinstance(md.get(key), str) else ""
            if val and val.lower() not in ("staff", "unknown"):
                name = val
                break

    phone_label = _format_phone_label(phone)
    if not name and phone_label:
        name = phone_label
    if not name:
        name = "Unknown sender"
    return name, phone_label


class StaffRequestListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer used for the inbox list view.

    The detail serializer below pulls the full CustomUserSerializer (with nested
    restaurant + profile) for the staff AND for every comment author, which
    caused N+1 storms on the inbox endpoint and was the reason the page hung on
    "Loading…". The list view only needs compact info per row; the full payload
    (comments + full staff object) is fetched on row click via retrieve().
    """
    staff_display_name = serializers.SerializerMethodField()
    staff_phone_display = serializers.SerializerMethodField()
    assignee_summary = serializers.SerializerMethodField()

    class Meta:
        model = StaffRequest
        fields = [
            'id',
            'staff',
            'staff_name',
            'staff_display_name',
            'staff_phone',
            'staff_phone_display',
            'category',
            'priority',
            'status',
            'subject',
            'description',
            'source',
            'external_id',
            'assignee',
            'assignee_summary',
            'voice_audio_url',
            'transcription_language',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields

    def get_staff_display_name(self, obj):
        name, _ = resolve_staff_request_sender(obj)
        return name

    def get_staff_phone_display(self, obj):
        _, phone = resolve_staff_request_sender(obj)
        return phone

    def get_assignee_summary(self, obj):
        if not obj.assignee_id:
            return None
        a = getattr(obj, 'assignee', None)
        if not a:
            return None
        first = getattr(a, 'first_name', '') or ''
        last = getattr(a, 'last_name', '') or ''
        name = f"{first} {last}".strip() or getattr(a, 'email', '') or ''
        return {
            'id': str(obj.assignee_id),
            'name': name,
            'email': getattr(a, 'email', '') or '',
            'role': getattr(a, 'role', '') or '',
        }


class StaffRequestSerializer(serializers.ModelSerializer):
    staff_details = CustomUserSerializer(source='staff', read_only=True)
    staff_display_name = serializers.SerializerMethodField()
    staff_phone_display = serializers.SerializerMethodField()
    comments = StaffRequestCommentSerializer(many=True, read_only=True)
    assignee_details = CustomUserSerializer(source='assignee', read_only=True)

    class Meta:
        model = StaffRequest
        fields = [
            'id',
            'restaurant',
            'staff',
            'staff_details',
            'staff_name',
            'staff_display_name',
            'staff_phone',
            'staff_phone_display',
            'category',
            'priority',
            'status',
            'subject',
            'description',
            'source',
            'external_id',
            'metadata',
            'assignee',
            'assignee_details',
            'voice_audio_url',
            'transcription',
            'transcription_language',
            'follow_up_date',
            'waiting_reason',
            'created_at',
            'updated_at',
            'reviewed_by',
            'reviewed_at',
            'comments',
        ]
        read_only_fields = [
            'id',
            'restaurant',
            'staff',
            'staff_details',
            'staff_display_name',
            'staff_phone_display',
            'assignee_details',
            'voice_audio_url',
            'transcription',
            'transcription_language',
            'created_at',
            'updated_at',
            'reviewed_by',
            'reviewed_at',
            'comments',
        ]

    def get_staff_display_name(self, obj):
        name, _ = resolve_staff_request_sender(obj)
        return name

    def get_staff_phone_display(self, obj):
        _, phone = resolve_staff_request_sender(obj)
        return phone
        
    def create(self, validated_data):
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            validated_data['recognized_by'] = request.user
            
        return super().create(validated_data)

# NOTE: Removed POS-related serializer stubs accidentally placed here (TableSerializer,
# OrderCreateSerializer, alternate ScheduleSerializer) to avoid import-time errors.
