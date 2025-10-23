from rest_framework import serializers
from .models import CustomUser, Restaurant, StaffInvitation, StaffProfile

class RestaurantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Restaurant
        fields = '__all__'

class CustomUserSerializer(serializers.ModelSerializer):
    restaurant_name = serializers.CharField(source='restaurant.name', read_only=True)

    class Meta:
        model = CustomUser
        fields = ['id', 'email', 'first_name', 'last_name', 'role', 'phone', 'restaurant', 'restaurant_name', 'is_verified', 'created_at', 'updated_at']
        read_only_fields = ['id', 'is_verified', 'created_at', 'updated_at', 'restaurant_name']


class StaffInvitationSerializer(serializers.ModelSerializer):
    class Meta:
        model = StaffInvitation
        fields = ['id', 'email', 'role', 'restaurant', 'invited_by', 'token', 'is_accepted', 'created_at', 'expires_at']
        read_only_fields = ['id', 'token', 'is_accepted', 'created_at', 'expires_at']


class StaffProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = StaffProfile
        fields = '__all__'
        read_only_fields = ['user']
    