from rest_framework import serializers
from .models import (
    ScheduleTemplate, TemplateShift, AssignedShift, WeeklySchedule, 
    ShiftSwapRequest, TaskCategory, ShiftTask, Timesheet, TimesheetEntry,
    TemplateVersion
)
from .task_templates import TaskTemplate, Task
from .audit import AuditLog

class TemplateShiftSerializer(serializers.ModelSerializer):
    class Meta:
        model = TemplateShift
        fields = '__all__'

class ScheduleTemplateSerializer(serializers.ModelSerializer):
    shifts = TemplateShiftSerializer(many=True, read_only=True)

    class Meta:
        model = ScheduleTemplate
        fields = '__all__'


class TemplateVersionSerializer(serializers.ModelSerializer):
    template_name = serializers.CharField(source='template.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    
    class Meta:
        model = TemplateVersion
        fields = [
            'id', 'template', 'template_name', 'version_number', 'status',
            'description', 'changes_summary', 'created_by', 'created_by_name',
            'created_at', 'activated_at', 'archived_at', 'template_data', 'shifts_data'
        ]
        read_only_fields = ['id', 'created_at', 'activated_at', 'archived_at']


class ScheduleTemplateDetailSerializer(ScheduleTemplateSerializer):
    """Detailed serializer with version information"""
    versions = TemplateVersionSerializer(many=True, read_only=True)
    
    class Meta(ScheduleTemplateSerializer.Meta):
        fields = '__all__'

class TaskCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskCategory
        fields = ['id', 'name', 'description', 'color', 'created_at']
        read_only_fields = ['id', 'created_at']
    
    def validate_name(self, value):
        """Validate category name length"""
        if not value or len(value) > 100:
            raise serializers.ValidationError("Category name must be between 1 and 100 characters.")
        return value
    
    def validate_description(self, value):
        """Validate description length"""
        if value and len(value) > 500:
            raise serializers.ValidationError("Description cannot exceed 500 characters.")
        return value
    
    def validate_color(self, value):
        """Validate color is a valid hex code"""
        import re
        if value and not re.match(r'^#[0-9A-Fa-f]{6}$', value):
            raise serializers.ValidationError("Color must be a valid hex code (e.g., #FF0000).")
        return value

class ShiftTaskSerializer(serializers.ModelSerializer):
    category_details = TaskCategorySerializer(source='category', read_only=True)
    assigned_to_name = serializers.CharField(source='assigned_to.get_full_name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    progress_percentage = serializers.SerializerMethodField()
    subtasks = serializers.SerializerMethodField()
    
    class Meta:
        model = ShiftTask
        fields = [
            'id', 'shift', 'category', 'category_details', 'title', 'description',
            'priority', 'status', 'assigned_to', 'assigned_to_name', 'estimated_duration',
            'parent_task', 'notes', 'created_by', 'created_by_name', 'created_at',
            'updated_at', 'completed_at', 'progress_percentage', 'subtasks'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'completed_at']
    
    def validate_title(self, value):
        """Validate task title length"""
        if not value or len(value) > 200:
            raise serializers.ValidationError("Task title must be between 1 and 200 characters.")
        return value
    
    def validate_description(self, value):
        """Validate description length"""
        if value and len(value) > 1000:
            raise serializers.ValidationError("Description cannot exceed 1000 characters.")
        return value
    
    def validate_estimated_duration(self, value):
        """Validate estimated duration is positive"""
        if value is not None and value <= 0:
            raise serializers.ValidationError("Estimated duration must be greater than zero.")
        return value
    
    def validate_notes(self, value):
        """Validate notes length"""
        if value and len(value) > 500:
            raise serializers.ValidationError("Notes cannot exceed 500 characters.")
        return value
    
    def get_progress_percentage(self, obj):
        return obj.get_progress_percentage()
    
    def get_subtasks(self, obj):
        if obj.parent_task is None:
            subtasks = obj.subtasks.all()
            return ShiftTaskSerializer(subtasks, many=True, read_only=True).data
        return []

class TaskTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskTemplate
        fields = '__all__'
        # Ensure server sets these so clients don't need to send them
        read_only_fields = ['id', 'restaurant', 'created_by', 'created_at', 'updated_at']

    def create(self, validated_data):
        # Inject restaurant and created_by from request context
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            restaurant = getattr(request.user, 'restaurant', None)
            if restaurant is None:
                # Return a clear 400 instead of 500 when user has no restaurant
                raise serializers.ValidationError({'restaurant': 'User has no associated restaurant.'})
            validated_data['restaurant'] = restaurant
            validated_data['created_by'] = request.user
        return super().create(validated_data)

class TaskSerializer(serializers.ModelSerializer):
    assigned_to_details = serializers.SerializerMethodField()
    category_details = TaskCategorySerializer(source='category', read_only=True)
    subtasks_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Task
        fields = '__all__'
        
    def get_subtasks_count(self, obj):
        return obj.subtasks.count()
        
    def get_assigned_to_details(self, obj):
        from accounts.serializers import UserSerializer
        return UserSerializer(obj.assigned_to.all(), many=True).data

class AssignedShiftSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source='staff.__str__', read_only=True)
    tasks = ShiftTaskSerializer(many=True, read_only=True)
    
    class Meta:
        model = AssignedShift
        fields = ['id', 'schedule', 'staff', 'staff_name', 'shift_date', 'start_time', 
                 'end_time', 'break_duration', 'role', 'notes', 'color', 'created_at', 'updated_at', 'tasks']
        # schedule comes from the nested URL (or explicitly in v2); clients of the nested endpoint shouldn't send it
        read_only_fields = ['id', 'created_at', 'updated_at', 'schedule']
    
    def validate_break_duration(self, value):
        """Validate break duration is non-negative"""
        if value is not None and value < 0:
            raise serializers.ValidationError("Break duration cannot be negative.")
        return value
    
    def validate_role(self, value):
        """Validate role length"""
        if value and len(value) > 100:
            raise serializers.ValidationError("Role cannot exceed 100 characters.")
        return value
    
    def validate_notes(self, value):
        """Validate notes length"""
        if value and len(value) > 500:
            raise serializers.ValidationError("Notes cannot exceed 500 characters.")
        return value
    
    def validate_color(self, value):
        """Validate color is a valid hex code"""
        import re
        if value and not re.match(r'^#[0-9A-Fa-f]{6}$', value):
            raise serializers.ValidationError("Color must be a valid hex code (e.g., #FF0000).")
        return value
    
    def validate(self, data):
        """Cross-field validation"""
        if 'start_time' in data and 'end_time' in data:
            if data['start_time'] >= data['end_time']:
                raise serializers.ValidationError("end_time must be after start_time.")
        return data

class WeeklyScheduleSerializer(serializers.ModelSerializer):
    assigned_shifts = AssignedShiftSerializer(many=True, read_only=True)
    
    class Meta:
        model = WeeklySchedule
        fields = '__all__'
        # Restaurant is injected server-side in views.perform_create; clients shouldn't send it
        read_only_fields = ['restaurant']

    def validate(self, data):
        # Optional: ensure week_start is a Monday
        week_start = data.get('week_start')
        if week_start is not None and hasattr(week_start, 'weekday') and week_start.weekday() != 0:
            raise serializers.ValidationError({
                'week_start': 'week_start must be a Monday (weekday=0).'
            })

        # Friendly pre-check for uniqueness to avoid DB 500s
        request = self.context.get('request')
        if request and hasattr(request, 'user') and getattr(request.user, 'restaurant', None) and week_start is not None:
            from .models import WeeklySchedule
            exists = WeeklySchedule.objects.filter(
                restaurant=request.user.restaurant,
                week_start=week_start
            ).exists()
            if exists:
                raise serializers.ValidationError({
                    'week_start': 'A weekly schedule for this week already exists.'
                })
        return data

class ShiftSwapRequestSerializer(serializers.ModelSerializer):
    shift_to_swap_details = AssignedShiftSerializer(source='shift_to_swap', read_only=True)
    requester_details = serializers.CharField(source='requester.__str__', read_only=True)
    receiver_details = serializers.CharField(source='receiver.__str__', read_only=True)

    class Meta:
        model = ShiftSwapRequest
        fields = '__all__'


# Enhanced serializers for AI scheduling
class AIScheduleRequestSerializer(serializers.Serializer):
    """Serializer for AI schedule generation requests"""
    week_start = serializers.DateField()
    labor_budget = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    demand_override = serializers.DictField(
        child=serializers.ChoiceField(choices=['LOW', 'MEDIUM', 'HIGH']),
        required=False
    )
    
    def validate_week_start(self, value):
        """Ensure week_start is a Monday"""
        if value.weekday() != 0:
            raise serializers.ValidationError("week_start must be a Monday")
        return value


# Timesheet Serializers
class TimesheetEntrySerializer(serializers.ModelSerializer):
    shift_details = AssignedShiftSerializer(source='shift', read_only=True)
    
    class Meta:
        model = TimesheetEntry
        fields = ['id', 'shift', 'shift_details', 'hours_worked', 'notes', 'created_at']
        read_only_fields = ['id', 'created_at']
    
    def validate_hours_worked(self, value):
        """Validate hours worked is positive"""
        if value <= 0:
            raise serializers.ValidationError("Hours worked must be greater than zero.")
        return value
    
    def validate_notes(self, value):
        """Validate notes length"""
        if value and len(value) > 500:
            raise serializers.ValidationError("Notes cannot exceed 500 characters.")
        return value


class TimesheetSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source='staff.get_full_name', read_only=True)
    approved_by_name = serializers.CharField(source='approved_by.get_full_name', read_only=True, allow_null=True)
    entries = TimesheetEntrySerializer(many=True, read_only=True)
    
    class Meta:
        model = Timesheet
        fields = [
            'id', 'staff', 'staff_name', 'restaurant', 'start_date', 'end_date',
            'total_hours', 'total_earnings', 'hourly_rate', 'status', 'notes',
            'submitted_at', 'approved_at', 'approved_by', 'approved_by_name',
            'paid_at', 'created_at', 'updated_at', 'entries'
        ]
        read_only_fields = [
            'id', 'total_hours', 'total_earnings', 'submitted_at',
            'approved_at', 'approved_by', 'paid_at', 'created_at', 'updated_at'
        ]
    
    def validate_hourly_rate(self, value):
        """Validate hourly rate is positive"""
        if value <= 0:
            raise serializers.ValidationError("Hourly rate must be greater than zero.")
        return value
    
    def validate_notes(self, value):
        """Validate notes length"""
        if value and len(value) > 1000:
            raise serializers.ValidationError("Notes cannot exceed 1000 characters.")
        return value
    
    def validate(self, data):
        """Cross-field validation"""
        if data.get('start_date') and data.get('end_date'):
            if data['start_date'] >= data['end_date']:
                raise serializers.ValidationError("End date must be after start date.")
        
        return data

class AuditLogSerializer(serializers.ModelSerializer):
    """Serializer for audit log entries"""
    user_name = serializers.CharField(source='user.get_full_name', read_only=True)
    user_email = serializers.CharField(source='user.email', read_only=True)
    content_type_name = serializers.CharField(source='content_type.model', read_only=True)
    object_str = serializers.SerializerMethodField()
    
    class Meta:
        model = AuditLog
        fields = [
            'id', 'timestamp', 'user', 'user_name', 'user_email', 'action', 'severity',
            'description', 'content_type', 'content_type_name', 'object_id', 'object_str',
            'old_values', 'new_values', 'metadata', 'ip_address', 'user_agent'
        ]
        read_only_fields = ['id', 'timestamp']
    
    def get_object_str(self, obj):
        """Get string representation of the audited object"""
        if obj.content_object:
            return str(obj.content_object)
        return None
