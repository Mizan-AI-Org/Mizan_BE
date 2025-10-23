from rest_framework import serializers
from .models import DailyKPI, Alert, Task
from accounts.serializers import CustomUserSerializer

class DailyKPISerializer(serializers.ModelSerializer):
    class Meta:
        model = DailyKPI
        fields = '__all__'
        read_only_fields = ('restaurant', 'created_at', 'updated_at')

class AlertSerializer(serializers.ModelSerializer):
    class Meta:
        model = Alert
        fields = '__all__'
        read_only_fields = ('restaurant', 'created_at')

class TaskSerializer(serializers.ModelSerializer):
    assigned_to_info = CustomUserSerializer(source='assigned_to', read_only=True)

    class Meta:
        model = Task
        fields = '__all__'
        read_only_fields = ('restaurant', 'created_at', 'updated_at')
