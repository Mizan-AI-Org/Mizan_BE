from rest_framework import serializers
from .models import Report
from accounts.serializers import RestaurantSerializer, UserSerializer # Assuming these exist

class ReportSerializer(serializers.ModelSerializer):
    restaurant_info = RestaurantSerializer(source='restaurant', read_only=True)
    generated_by_info = UserSerializer(source='generated_by', read_only=True)

    class Meta:
        model = Report
        fields = ('id', 'restaurant', 'restaurant_info', 'report_type', 'generated_at', 'data', 'generated_by', 'generated_by_info')
        read_only_fields = ('restaurant', 'generated_at', 'generated_by', 'restaurant_info', 'generated_by_info')
