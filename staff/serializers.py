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


class StaffRequestSerializer(serializers.ModelSerializer):
    staff_details = CustomUserSerializer(source='staff', read_only=True)
    comments = StaffRequestCommentSerializer(many=True, read_only=True)

    class Meta:
        model = StaffRequest
        fields = [
            'id',
            'restaurant',
            'staff',
            'staff_details',
            'staff_name',
            'staff_phone',
            'category',
            'priority',
            'status',
            'subject',
            'description',
            'source',
            'external_id',
            'metadata',
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
            'created_at',
            'updated_at',
            'reviewed_by',
            'reviewed_at',
            'comments',
        ]
        
    def create(self, validated_data):
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            validated_data['recognized_by'] = request.user
            
        return super().create(validated_data)

# NOTE: Removed POS-related serializer stubs accidentally placed here (TableSerializer,
# OrderCreateSerializer, alternate ScheduleSerializer) to avoid import-time errors.
