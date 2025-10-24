from rest_framework import serializers
from .models import ScheduleTemplate, TemplateShift, AssignedShift, WeeklySchedule, ShiftSwapRequest

class TemplateShiftSerializer(serializers.ModelSerializer):
    class Meta:
        model = TemplateShift
        fields = '__all__'

class ScheduleTemplateSerializer(serializers.ModelSerializer):
    shifts = TemplateShiftSerializer(many=True, read_only=True)

    class Meta:
        model = ScheduleTemplate
        fields = '__all__'

class AssignedShiftSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source='staff.__str__', read_only=True)
    class Meta:
        model = AssignedShift
        fields = '__all__'

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
