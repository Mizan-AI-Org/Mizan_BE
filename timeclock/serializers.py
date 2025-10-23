from rest_framework import serializers

from accounts.models import CustomUser
from accounts.utils import validate_clockin_location
from .models import ClockEvent
from scheduling.models import AssignedShift

class ClockEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClockEvent
        fields = '__all__'

class ShiftSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssignedShift
        fields = '__all__'
class ClockInSerializer(serializers.Serializer):
    pin_code = serializers.CharField(max_length=6)
    latitude = serializers.FloatField(required=True)
    longitude = serializers.FloatField(required=True)
    photo = serializers.ImageField(required=False)
    accuracy = serializers.FloatField(required=False)  # GPS accuracy in meters
    
    def validate(self, attrs):
        pin_code = attrs.get('pin_code')
        latitude = attrs.get('latitude')
        longitude = attrs.get('longitude')
        
        # Validate PIN code
        try:
            user = CustomUser.objects.get(pin_code=pin_code, is_active=True)
        except CustomUser.DoesNotExist:
            raise serializers.ValidationError('Invalid PIN code')
        
        # Validate location
        is_valid, message = validate_clockin_location(
            user.restaurant, 
            latitude, 
            longitude
        )
        
        if not is_valid:
            raise serializers.ValidationError(message)
        
        # Check GPS accuracy (optional but recommended)
        accuracy = attrs.get('accuracy')
        if accuracy and accuracy > 50:  # If accuracy worse than 50 meters
            raise serializers.ValidationError('GPS signal too weak. Please move to a better location.')
        
        attrs['user'] = user
        return attrs