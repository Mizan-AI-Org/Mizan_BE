from rest_framework import serializers
from .models import ScheduleTemplate, TemplateShift, WeeklySchedule, AssignedShift, ShiftSwapRequest
from accounts.serializers import CustomUserSerializer

class ScheduleTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScheduleTemplate
        fields = '__all__'
        read_only_fields = ('restaurant', 'created_at')

class TemplateShiftSerializer(serializers.ModelSerializer):
    class Meta:
        model = TemplateShift
        fields = '__all__'
        read_only_fields = ('template',)

class WeeklyScheduleSerializer(serializers.ModelSerializer):
    class Meta:
        model = WeeklySchedule
        fields = '__all__'
        read_only_fields = ('restaurant', 'created_at')

class AssignedShiftSerializer(serializers.ModelSerializer):
    staff_info = CustomUserSerializer(source='staff', read_only=True)

    class Meta:
        model = AssignedShift
        fields = '__all__'
        read_only_fields = ('schedule', 'created_at', 'updated_at', 'actual_hours')

class ShiftSwapRequestSerializer(serializers.ModelSerializer):
    shift_to_swap_info = AssignedShiftSerializer(source='shift_to_swap', read_only=True)
    requester_info = CustomUserSerializer(source='requester', read_only=True)
    receiver_info = CustomUserSerializer(source='receiver', read_only=True)

    class Meta:
        model = ShiftSwapRequest
        fields = '__all__'
        read_only_fields = ('status', 'created_at', 'updated_at')