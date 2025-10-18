from rest_framework import serializers
from django.contrib.auth import authenticate
from .models import CustomUser, Restaurant, StaffProfile

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
    
    class Meta:
        model = CustomUser
        fields = ('id', 'username', 'email', 'role', 'pin_code', 'restaurant', 
                 'restaurant_name', 'phone', 'is_active', 'profile')
        read_only_fields = ('pin_code',)

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
    