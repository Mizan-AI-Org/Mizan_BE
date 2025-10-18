from rest_framework import serializers
from .models import ScheduleTemplate, TemplateShift, WeeklySchedule
from timeclock.models import Shift
from accounts.serializers import UserSerializer

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
    class Meta:
        model = WeeklySchedule
        fields = '__all__'

class ShiftAssignmentSerializer(serializers.ModelSerializer):
    staff_info = UserSerializer(source='staff', read_only=True)
    
    class Meta:
        model = Shift
        fields = '__all__'