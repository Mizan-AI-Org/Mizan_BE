from rest_framework import serializers
from .models import POSIntegration, AIAssistantConfig, Restaurant, StaffProfile


class POSIntegrationSerializer(serializers.ModelSerializer):
    restaurant_name = serializers.CharField(source='restaurant.name', read_only=True)
    
    class Meta:
        model = POSIntegration
        fields = [
            'id',
            'restaurant',
            'restaurant_name',
            'last_sync_time',
            'sync_status',
            'total_transactions_synced',
            'created_at',
            'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class AIAssistantConfigSerializer(serializers.ModelSerializer):
    restaurant_name = serializers.CharField(source='restaurant.name', read_only=True)
    
    class Meta:
        model = AIAssistantConfig
        fields = [
            'id',
            'restaurant',
            'restaurant_name',
            'enabled',
            'ai_provider',
            'features_enabled',
            'created_at',
            'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'api_key']


class RestaurantGeolocationSerializer(serializers.ModelSerializer):
    """Serializer for restaurant geolocation and settings"""
    
    class Meta:
        model = Restaurant
        fields = [
            'id',
            'name',
            'latitude',
            'longitude',
            'radius',
            'geofence_enabled',
            'geofence_polygon',
            'pos_provider',
            'pos_is_connected',
            'timezone',
            'currency',
            'language',
            'operating_hours'
        ]
    
    def validate_radius(self, value):
        """Validate radius is within 5m to 100m range"""
        if value is not None:
            if value < 5 or value > 100:
                raise serializers.ValidationError(
                    "Geofence radius must be between 5 and 100 meters."
                )
        return value


class RestaurantSettingsSerializer(serializers.ModelSerializer):
    """Comprehensive restaurant settings serializer"""
    pos_integration = POSIntegrationSerializer(read_only=True)
    ai_config = AIAssistantConfigSerializer(read_only=True)
    geolocation_locked = serializers.SerializerMethodField()
    
    class Meta:
        model = Restaurant
        fields = [
            'id',
            'name',
            'address',
            'phone',
            'email',
            'latitude',
            'longitude',
            'radius',
            'geofence_enabled',
            'geofence_polygon',
            'geolocation_locked',
            'timezone',
            'currency',
            'language',
            'operating_hours',
            'automatic_clock_out',
            'break_duration',
            'email_notifications',
            'push_notifications',
            'pos_provider',
            'pos_merchant_id',
            'pos_is_connected',
            'pos_integration',
            'ai_config'
        ]
    
    def validate_radius(self, value):
        """Validate radius is within 5m to 100m range"""
        if value is not None:
            if value < 5 or value > 100:
                raise serializers.ValidationError(
                    "Geofence radius must be between 5 and 100 meters."
                )
        return value

    def get_geolocation_locked(self, obj):
        """Return True if coordinates are set and non-SUPER_ADMIN cannot change."""
        has_coords = obj.latitude is not None and obj.longitude is not None
        request = self.context.get('request') if hasattr(self, 'context') else None
        user_role = getattr(getattr(request, 'user', None), 'role', None)
        # Locked for everyone except SUPER_ADMIN when coordinates exist
        if has_coords and user_role != 'SUPER_ADMIN':
            return True
        return False


class StaffProfileExtendedSerializer(serializers.ModelSerializer):
    user_id = serializers.CharField(source='user.id', read_only=True)
    user_email = serializers.CharField(source='user.email', read_only=True)
    user_name = serializers.SerializerMethodField()
    
    class Meta:
        model = StaffProfile
        fields = [
            'user_id',
            'user_email',
            'user_name',
            'contract_end_date',
            'health_card_expiry',
            'hourly_rate',
            'emergency_contact_name',
            'emergency_contact_phone',
            'notes',
            'last_location_latitude',
            'last_location_longitude',
            'last_location_timestamp',
            'geofence_alerts_enabled'
        ]
    
    def get_user_name(self, obj):
        return f"{obj.user.first_name} {obj.user.last_name}"