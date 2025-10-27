from rest_framework import serializers
from .models import (
    ScheduleTemplate, TemplateShift, AssignedShift, WeeklySchedule, 
    ShiftSwapRequest, TaskCategory, ShiftTask
)

class TemplateShiftSerializer(serializers.ModelSerializer):
    class Meta:
        model = TemplateShift
        fields = '__all__'

class ScheduleTemplateSerializer(serializers.ModelSerializer):
    shifts = TemplateShiftSerializer(many=True, read_only=True)

    class Meta:
        model = ScheduleTemplate
        fields = '__all__'

class TaskCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskCategory
        fields = ['id', 'name', 'description', 'color', 'created_at']
        read_only_fields = ['id', 'created_at']

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
    
    def get_progress_percentage(self, obj):
        return obj.get_progress_percentage()
    
    def get_subtasks(self, obj):
        if obj.parent_task is None:
            subtasks = obj.subtasks.all()
            return ShiftTaskSerializer(subtasks, many=True, read_only=True).data
        return []

class AssignedShiftSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source='staff.__str__', read_only=True)
    tasks = ShiftTaskSerializer(many=True, read_only=True)
    
    class Meta:
        model = AssignedShift
        fields = ['id', 'schedule', 'staff', 'staff_name', 'shift_date', 'start_time', 
                 'end_time', 'break_duration', 'role', 'notes', 'color', 'created_at', 'updated_at', 'tasks']
        read_only_fields = ['id', 'created_at', 'updated_at']

class WeeklyScheduleSerializer(serializers.ModelSerializer):
    assigned_shifts = AssignedShiftSerializer(many=True, read_only=True)
    
    class Meta:
        model = WeeklySchedule
        fields = '__all__'

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
