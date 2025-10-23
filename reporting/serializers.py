from rest_framework import serializers
from .models import DailySalesReport, AttendanceReport, InventoryReport

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
