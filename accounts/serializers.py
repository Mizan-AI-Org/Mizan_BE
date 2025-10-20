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
    profile = StaffProfileSerializer(required=False)
    restaurant_name = serializers.CharField(source='restaurant.name', read_only=True)
    restaurant = RestaurantSerializer(read_only=True) # Nested serializer for restaurant details
    
    class Meta:
        model = CustomUser
        fields = ('id', 'email', 'first_name', 'last_name', 'role', 'restaurant', 
                  'restaurant_name', 'phone', 'is_active', 'is_verified', 'created_at', 'profile')
        read_only_fields = ('id', 'created_at',)
        extra_kwargs = {'password': {'write_only': True}}
        
    def create(self, validated_data):
        profile_data = validated_data.pop('profile', None)
        password = validated_data.pop('password', None)
        user = CustomUser.objects.create_user(**validated_data, password=password)
        if profile_data:
            StaffProfile.objects.create(user=user, **profile_data)
        return user

class StaffInvitationSerializer(serializers.ModelSerializer):
    class Meta:
        model = StaffInvitation
        fields = '__all__'
        read_only_fields = ('id', 'invited_by', 'restaurant', 'token', 'is_accepted', 'created_at', 'expires_at')

class PinLoginSerializer(serializers.Serializer):
    pin_code = serializers.CharField(max_length=6)
    latitude = serializers.FloatField(required=False, allow_null=True)
    longitude = serializers.FloatField(required=False, allow_null=True)
    image_data = serializers.CharField(required=False, allow_null=True)
    
    def validate(self, attrs):
        pin_code = attrs.get('pin_code')
        latitude = attrs.get('latitude')
        longitude = attrs.get('longitude')
        
        user = CustomUser.objects.filter(is_active=True, pin_code__isnull=False).first()
        if not user or not user.check_pin(pin_code):
            raise serializers.ValidationError('Invalid PIN code')
        
        # Basic geofencing check (simplified)
        restaurant = user.restaurant
        # In production, implement proper geofencing logic here
        if restaurant.latitude and restaurant.longitude and restaurant.geo_fence_radius:
            from geopy.distance import geodesic
            restaurant_coords = (restaurant.latitude, restaurant.longitude)
            user_coords = (latitude, longitude)
            distance = geodesic(restaurant_coords, user_coords).meters
            
            if distance > restaurant.geo_fence_radius:
                raise serializers.ValidationError("You are not within the restaurant premises.")
        
        attrs['user'] = user
        return attrs
    