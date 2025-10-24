from rest_framework import serializers
from .models import CustomUser, Restaurant, StaffInvitation, StaffProfile
from django.contrib.auth import authenticate

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


class PinLoginSerializer(serializers.Serializer):
    pin_code = serializers.CharField(max_length=6)
    email = serializers.EmailField(required=False, allow_blank=True)

    def validate(self, data):
        email = data.get('email')
        pin_code = data.get('pin_code')

        if not pin_code:
            raise serializers.ValidationError("PIN code is required.")

        # Authenticate by pin code only
        user = CustomUser.objects.filter(pin_code__isnull=False, is_active=True).first()
        if user and user.check_pin(pin_code):
            data['user'] = user
        else:
            raise serializers.ValidationError("Invalid PIN code or inactive user.")

        return data 