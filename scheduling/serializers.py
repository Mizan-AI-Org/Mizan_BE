from rest_framework import serializers
from .models import ScheduleTemplate, TemplateShift, WeeklySchedule, AssignedShift, ShiftSwapRequest
from accounts.serializers import UserSerializer

class AssignedShiftSerializer(serializers.ModelSerializer):
    staff_info = UserSerializer(source='staff', read_only=True)
    
    class Meta:
        model = AssignedShift
        fields = '__all__'

class ShiftSwapRequestSerializer(serializers.ModelSerializer):
    requester_info = UserSerializer(source='requester', read_only=True)
    receiver_info = UserSerializer(source='receiver', read_only=True)
    shift_to_swap_info = AssignedShiftSerializer(source='shift_to_swap', read_only=True)

    class Meta:
        model = ShiftSwapRequest
        fields = '__all__'
        read_only_fields = ('id', 'requester', 'status', 'created_at', 'updated_at')

class ShiftSwapRequestCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShiftSwapRequest
        fields = ('shift_to_swap', 'request_message', 'receiver')
        extra_kwargs = {'receiver': {'required': False, 'allow_null': True}}

class TemplateShiftSerializer(serializers.ModelSerializer):
    class Meta:
        model = TemplateShift
        fields = '__all__'

class ScheduleTemplateSerializer(serializers.ModelSerializer):
    shifts = TemplateShiftSerializer(many=True, read_only=True)
    
    class Meta:
        model = ScheduleTemplate
        fields = '__all__'

class WeeklyScheduleSerializer(serializers.ModelSerializer):
    assigned_shifts = AssignedShiftSerializer(many=True, read_only=True) # Nested serializer
    
    class Meta:
        model = WeeklySchedule
        fields = '__all__'

class ShiftAssignmentSerializer(serializers.ModelSerializer):
    staff_info = UserSerializer(source='staff', read_only=True)
    
    class Meta:
        model = AssignedShift # Use AssignedShift model
        fields = '__all__'