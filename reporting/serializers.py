from rest_framework import serializers
from .models import DailySalesReport, AttendanceReport, InventoryReport, Incident, LaborBudget, LaborPolicy

class DailySalesReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = DailySalesReport
        fields = '__all__'
        read_only_fields = ('id', 'restaurant', 'created_at', 'updated_at')

class AttendanceReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceReport
        fields = '__all__'
        read_only_fields = ('id', 'restaurant', 'created_at', 'updated_at')

class InventoryReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = InventoryReport
        fields = '__all__'
        read_only_fields = ('id', 'restaurant', 'created_at', 'updated_at')

class IncidentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Incident
        fields = '__all__'
        read_only_fields = ('id', 'restaurant', 'created_at', 'updated_at')


class LaborBudgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = LaborBudget
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')


class LaborPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = LaborPolicy
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')
