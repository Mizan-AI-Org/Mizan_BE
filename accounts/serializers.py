from rest_framework import serializers
from .models import (
    CustomUser, Restaurant, UserInvitation, StaffProfile, StaffInvitation, AuditLog
)
from django.contrib.auth import authenticate
import sys

class StaffProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = StaffProfile
        fields = ['hourly_rate', 'salary_type', 'join_date', 'promotion_history', 'emergency_contact_name', 'emergency_contact_phone', 'notes', 'department']
        extra_kwargs = {
            'join_date': {'required': False, 'allow_null': True},
            'department': {'required': False, 'allow_null': True, 'allow_blank': True},
            'emergency_contact_name': {'required': False, 'allow_null': True, 'allow_blank': True},
            'emergency_contact_phone': {'required': False, 'allow_null': True, 'allow_blank': True},
            'notes': {'required': False, 'allow_blank': True},
        }

class RestaurantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Restaurant
        fields = '__all__'

class CustomUserSerializer(serializers.ModelSerializer):
    restaurant_name = serializers.CharField(source='restaurant.name', read_only=True)
    profile = StaffProfileSerializer(required=False)
    email = serializers.EmailField(required=False)
    
    class Meta:
        model = CustomUser
        fields = ['id', 'email', 'first_name', 'last_name', 'role', 'phone', 'restaurant', 'restaurant_name', 'is_verified', 'created_at', 'updated_at', 'profile']
        read_only_fields = ['id', 'is_verified', 'created_at', 'updated_at', 'restaurant_name']
        extra_kwargs = {
            'first_name': {'required': False},
            'last_name': {'required': False},
            'role': {'required': False},
        }

    def validate_email(self, value):
        """
        Check that the email is unique, ignoring the current instance.
        """
        if self.instance and self.instance.email == value:
            return value
        if CustomUser.objects.filter(email=value).exists():
            raise serializers.ValidationError("A user with this email address already exists.")
        return value

    def update(self, instance, validated_data):
        profile_data = validated_data.pop('profile', None)
        
        # Update CustomUser fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        # Update or create StaffProfile
        if profile_data:
            profile, created = StaffProfile.objects.get_or_create(user=instance)
            profile_serializer = StaffProfileSerializer(profile, data=profile_data, partial=True)
            if profile_serializer.is_valid(raise_exception=True):
                profile_serializer.save()
        
        return instance


class StaffInvitationSerializer(serializers.ModelSerializer):
    phone_number = serializers.SerializerMethodField(read_only=True)
    class Meta:
        model = UserInvitation
        fields = [
            'id', 'email', 'role', 'restaurant', 'invited_by', 'invitation_token',
            'is_accepted', 'sent_at', 'expires_at', 'first_name', 'last_name', 'extra_data', 'phone_number'
        ]
        read_only_fields = [
            'id', 'invitation_token', 'is_accepted', 'sent_at', 'expires_at',
            'restaurant', 'invited_by'
        ]

    def get_phone_number(self, obj):
        data = getattr(obj, 'extra_data', {}) or {}
        # prefer explicit phone_number then phone
        return data.get('phone_number') or data.get('phone') or None




class PinLoginSerializer(serializers.Serializer):
    pin_code = serializers.CharField(max_length=4, min_length=4)
    email = serializers.EmailField(required=False, allow_blank=True)

    def validate_pin_code(self, value):
        """Validate PIN format - must be exactly 4 digits."""
        import re
        if not re.match(r'^\d{4}$', value):
            raise serializers.ValidationError("PIN must be exactly 4 digits.")
        return value

    def validate(self, data):
        email = data.get('email')
        pin_code = data.get('pin_code')

        if not pin_code:
            raise serializers.ValidationError("PIN code is required.")

        # If email is provided, authenticate by email + PIN
        # If email is not provided, authenticate by PIN only (for staff)
        if email:
            try:
                user = CustomUser.objects.get(email=email, is_active=True)
                
                # Check if account is locked
                if user.is_account_locked():
                    raise serializers.ValidationError("Account is temporarily locked due to multiple failed attempts. Please try again later.")
                
                # Check if user is staff (should have PIN)
                if not user.is_staff_role():
                    raise serializers.ValidationError("PIN authentication is only available for staff members.")
                
                if user.check_pin(pin_code):
                    data['user'] = user
                else:
                    raise serializers.ValidationError("Invalid PIN code.")
            except CustomUser.DoesNotExist:
                raise serializers.ValidationError("User not found or inactive.")
        else:
            # Authenticate by PIN only (for staff login)
            users_with_pin = CustomUser.objects.filter(
                pin_code__isnull=False, 
                is_active=True,
                role__in=['CHEF', 'WAITER', 'CLEANER', 'CASHIER', 'KITCHEN_HELP', 'BARTENDER', 'RECEPTIONIST', 'SECURITY']
            )
            
            authenticated_user = None
            for user in users_with_pin:
                if user.is_account_locked():
                    continue  # Skip locked accounts
                    
                if user.check_pin(pin_code):
                    authenticated_user = user
                    break
            
            if authenticated_user:
                data['user'] = authenticated_user
            else:
                raise serializers.ValidationError("Invalid PIN code or account locked.")

        return data


# Enhanced serializers for user management
class UserSerializer(serializers.ModelSerializer):
    """Enhanced user serializer with profile data"""
    restaurant_name = serializers.CharField(source='restaurant.name', read_only=True)
    role_display = serializers.CharField(source='get_role_display', read_only=True)
    restaurant_data = RestaurantSerializer(source='restaurant', read_only=True)
    profile = StaffProfileSerializer(read_only=True)
    
    class Meta:
        model = CustomUser
        fields = [
            'id', 'email', 'first_name', 'last_name', 'role', 'role_display',
            'phone', 'restaurant', 'restaurant_name', 'restaurant_data', 'is_verified', 'is_active',
            'created_at', 'updated_at', 'profile'
        ]
        read_only_fields = ['id', 'is_verified', 'created_at', 'updated_at', 'restaurant_name', 'role_display', 'restaurant_data']


class BulkInviteSerializer(serializers.Serializer):
    """Serializer for bulk invitation requests"""
    type = serializers.ChoiceField(choices=['csv', 'json'])
    csv_content = serializers.CharField(required=False, allow_blank=True)
    invitations = serializers.ListField(
        child=serializers.DictField(),
        required=False
    )
    
    def validate(self, data):
        invite_type = data.get('type')
        
        if invite_type == 'csv':
            if not data.get('csv_content'):
                raise serializers.ValidationError("csv_content is required for CSV type")
        elif invite_type == 'json':
            if not data.get('invitations'):
                raise serializers.ValidationError("invitations list is required for JSON type")
        
        return data


class AcceptInvitationSerializer(serializers.Serializer):
    """Serializer for accepting invitations"""
    token = serializers.CharField(max_length=100)
    password = serializers.CharField(min_length=8, write_only=True)
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)


class UpdateUserRoleSerializer(serializers.Serializer):
    """Serializer for updating user roles"""
    role = serializers.ChoiceField(choices=[
        ('SUPER_ADMIN', 'Super Admin'),
        ('ADMIN', 'Admin'),
        ('CHEF', 'Chef'),
        ('WAITER', 'Waiter'),
        ('CLEANER', 'Cleaner'),
        ('CASHIER', 'Cashier'),
    ])

class StaffSerializer(serializers.ModelSerializer):
    profile = StaffProfileSerializer(read_only=True)

    class Meta:
        model = CustomUser
        fields = ['id', 'email', 'first_name', 'last_name', 'role', 'phone', 'is_active', 'created_at', 'updated_at', 'profile']
        read_only_fields = ['id', 'created_at', 'updated_at']
