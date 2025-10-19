from rest_framework import serializers
from django.contrib.auth import authenticate
from .models import CustomUser, Restaurant, StaffProfile, StaffInvitation
from django.contrib.auth.password_validation import validate_password
class RestaurantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Restaurant
        fields = '__all__'

class StaffProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = StaffProfile
        fields = '__all__'

class UserSerializer(serializers.ModelSerializer):
    profile = StaffProfileSerializer(read_only=True)
    restaurant_name = serializers.CharField(source='restaurant.name', read_only=True)
    restaurant = RestaurantSerializer(read_only=True) # Nested serializer for restaurant details
    
    class Meta:
        model = CustomUser
        fields = ('id', 'email', 'first_name', 'last_name', 'role', 'restaurant', 
                  'restaurant_name', 'phone', 'is_active', 'is_verified', 'created_at', 'profile')
        read_only_fields = ('id', 'created_at',)
        extra_kwargs = {'password': {'write_only': True}}
        
    def create(self, validated_data):
        password = validated_data.pop('password', None)
        user = CustomUser.objects.create_user(**validated_data, password=password)
        return user

class StaffInvitationSerializer(serializers.ModelSerializer):
    class Meta:
        model = StaffInvitation
        fields = '__all__'
        read_only_fields = ('id', 'invited_by', 'restaurant', 'token', 'is_accepted', 'created_at', 'expires_at')

class PinLoginSerializer(serializers.Serializer):
    pin_code = serializers.CharField(max_length=6)
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    
    def validate(self, attrs):
        pin_code = attrs.get('pin_code')
        latitude = attrs.get('latitude')
        longitude = attrs.get('longitude')
        
        try:
            user = CustomUser.objects.get(pin_code=pin_code, is_active=True)
        except CustomUser.DoesNotExist:
            raise serializers.ValidationError('Invalid PIN code')
        
        # Basic geofencing check (simplified)
        restaurant = user.restaurant
        # In production, implement proper geofencing logic here
        
        attrs['user'] = user
        return attrs
    