from rest_framework import serializers
from accounts.models import CustomUser, Restaurant
from accounts.serializers import UserSerializer, RestaurantSerializer
from staff.models_safety import (
    StandardOperatingProcedure,
    SafetyChecklist,
    ScheduleTask,
    SafetyConcernReport,
    SafetyRecognition
)

class StandardOperatingProcedureSerializer(serializers.ModelSerializer):
    created_by_details = UserSerializer(source='created_by', read_only=True)
    restaurant_details = RestaurantSerializer(source='restaurant', read_only=True)
    
    class Meta:
        model = StandardOperatingProcedure
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at', 'created_by')
    
    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)

class SafetyChecklistSerializer(serializers.ModelSerializer):
    created_by_details = UserSerializer(source='created_by', read_only=True)
    restaurant_details = RestaurantSerializer(source='restaurant', read_only=True)
    
    class Meta:
        model = SafetyChecklist
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at', 'created_by')
    
    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)

class ScheduleTaskSerializer(serializers.ModelSerializer):
    sop_details = StandardOperatingProcedureSerializer(source='sop', read_only=True)
    checklist_details = SafetyChecklistSerializer(source='checklist', read_only=True)
    completed_by_details = UserSerializer(source='completed_by', read_only=True)
    
    class Meta:
        model = ScheduleTask
        fields = '__all__'
        read_only_fields = ('created_at', 'completed_at', 'completed_by')
    
    def validate(self, data):
        # Ensure at least one of SOP or checklist is provided
        if not data.get('sop') and not data.get('checklist') and not data.get('description'):
            raise serializers.ValidationError("At least one of SOP, checklist, or description must be provided")
        return data

class SafetyConcernReportSerializer(serializers.ModelSerializer):
    reporter_details = UserSerializer(source='reporter', read_only=True)
    resolved_by_details = UserSerializer(source='resolved_by', read_only=True)
    restaurant_details = RestaurantSerializer(source='restaurant', read_only=True)
    
    class Meta:
        model = SafetyConcernReport
        fields = '__all__'
        read_only_fields = ('created_at', 'resolved_at', 'resolved_by')
    
    def create(self, validated_data):
        # Get the request user
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            raise serializers.ValidationError("Authentication required")
            
        user = request.user
        
        # Automatically set the restaurant from user's restaurant
        if 'restaurant' not in validated_data:
            # Try to get restaurant from user's restaurant field
            if hasattr(user, 'restaurant') and user.restaurant:
                validated_data['restaurant'] = user.restaurant
            # Or get the first restaurant the user is associated with (if using many-to-many)
            elif hasattr(user, 'restaurants') and hasattr(user.restaurants, 'exists') and user.restaurants.exists():
                validated_data['restaurant'] = user.restaurants.first()
            else:
                raise serializers.ValidationError({
                    "restaurant": "User must be associated with a restaurant. Please contact your administrator."
                })
        
        # Handle anonymous reports - store the reporter but mark as anonymous
        if validated_data.get('is_anonymous', True):
            validated_data['reporter'] = user
        
        return super().create(validated_data)

class SafetyRecognitionSerializer(serializers.ModelSerializer):
    staff_details = UserSerializer(source='staff', read_only=True)
    awarded_by_details = UserSerializer(source='awarded_by', read_only=True)
    restaurant_details = RestaurantSerializer(source='restaurant', read_only=True)
    
    class Meta:
        model = SafetyRecognition
        fields = '__all__'
        read_only_fields = ('created_at', 'awarded_by')
    
    def create(self, validated_data):
        validated_data['awarded_by'] = self.context['request'].user
        return super().create(validated_data)