from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
import uuid
from django.contrib.auth.hashers import make_password, check_password
from django.conf import settings

class CustomUserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)
        is_verified = extra_fields.pop('is_verified', False) # Extract and remove is_verified
        user = self.model(email=email, is_verified=is_verified, **extra_fields)
        if password:
            user.set_password(password)
        if 'pin_code' in extra_fields and extra_fields['pin_code']:
            user.pin_code = make_password(extra_fields['pin_code'])
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        extra_fields.setdefault('role', 'SUPER_ADMIN')
        
        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')
        
        return self.create_user(email, password, **extra_fields)


class Restaurant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(unique=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    radius = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True, default=500)  # Default 500m perimeter
    geofence_enabled = models.BooleanField(default=True)
    geofence_polygon = models.JSONField(default=list, blank=True)  # Array of lat/lon coordinates for custom perimeter
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    timezone = models.CharField(max_length=50, default='America/New_York')
    currency = models.CharField(max_length=10, default='USD')
    language = models.CharField(max_length=10, default='en')
    operating_hours = models.JSONField(default=dict)
    automatic_clock_out = models.BooleanField(default=False)
    break_duration = models.IntegerField(default=30) # Default to 30 minutes
    email_notifications = models.JSONField(default=dict)
    push_notifications = models.JSONField(default=dict)
    
    # POS Integration Fields
    pos_provider = models.CharField(max_length=50, choices=[
        ('STRIPE', 'Stripe'),
        ('SQUARE', 'Square'),
        ('CLOVER', 'Clover'),
        ('CUSTOM', 'Custom API'),
        ('NONE', 'Not Configured')
    ], default='NONE')
    pos_merchant_id = models.CharField(max_length=255, blank=True, null=True)
    pos_api_key = models.CharField(max_length=255, blank=True, null=True)
    pos_is_connected = models.BooleanField(default=False)
    
    class Meta:
        db_table = 'restaurants'
    
    def __str__(self):
        return self.name

class CustomUser(AbstractUser):
    ROLE_CHOICES = settings.STAFF_ROLES_CHOICES
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pin_code = models.CharField(max_length=6, unique=True, blank=True, null=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    phone = models.CharField(max_length=20, blank=True, null=True)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='staff', null=True, blank=True)
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Remove username and use email instead
    username = None
    email = models.EmailField(unique=True)
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['first_name', 'last_name']
    
    objects = CustomUserManager() # Add this line
    
    class Meta:
        db_table = 'users'
    
    def __str__(self):
        return f"{self.get_full_name()} - {self.restaurant.name}" if self.restaurant else self.get_full_name()
        
    def set_pin(self, raw_pin):
        self.pin_code = make_password(raw_pin)
        
    def check_pin(self, raw_pin):
        return check_password(raw_pin, self.pin_code)

class StaffInvitation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField()
    role = models.CharField(max_length=20, choices=CustomUser.ROLE_CHOICES)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE)
    invited_by = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    token = models.CharField(max_length=100, unique=True)
    is_accepted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    
    class Meta:
        db_table = 'staff_invitations'


# ============================================================================
# RBAC MODELS - Multi-Tenant Role-Based Access Control
# ============================================================================

class Role(models.Model):
    """Custom roles for restaurants with fine-grained permissions"""
    
    ROLE_TYPES = (
        ('OWNER', 'Restaurant Owner'),
        ('MANAGER', 'Manager'),
        ('SUPERVISOR', 'Supervisor'),
        ('CHEF', 'Chef'),
        ('WAITER', 'Waiter/Server'),
        ('CASHIER', 'Cashier'),
        ('KITCHEN_STAFF', 'Kitchen Staff'),
        ('CLEANER', 'Cleaner/Housekeeping'),
        ('DELIVERY', 'Delivery Driver'),
        ('CUSTOM', 'Custom Role'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='roles')
    name = models.CharField(max_length=100, choices=ROLE_TYPES)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'rbac_roles'
        unique_together = ['restaurant', 'name']
        ordering = ['name']
    
    def __str__(self):
        return f"{self.get_name_display()} ({self.restaurant.name})"


class Permission(models.Model):
    """Fine-grained permissions for role-based access control"""
    
    PERMISSION_CATEGORIES = (
        ('USER_MANAGEMENT', 'User Management'),
        ('POS', 'Point of Sale'),
        ('INVENTORY', 'Inventory Management'),
        ('SCHEDULING', 'Staff Scheduling'),
        ('REPORTING', 'Reports & Analytics'),
        ('KITCHEN', 'Kitchen Operations'),
        ('ADMIN', 'Admin Settings'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='permissions')
    code = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    category = models.CharField(max_length=50, choices=PERMISSION_CATEGORIES)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'rbac_permissions'
        ordering = ['category', 'code']
    
    def __str__(self):
        return f"{self.code} - {self.name}"


class RolePermission(models.Model):
    """Junction table: Maps roles to permissions"""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name='permissions')
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE, related_name='roles')
    assigned_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'rbac_role_permissions'
        unique_together = ['role', 'permission']
    
    def __str__(self):
        return f"{self.role.get_name_display()} -> {self.permission.code}"


class UserRole(models.Model):
    """Maps users to roles in a restaurant (multi-tenancy support)"""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='restaurant_roles')
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='user_roles')
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name='users')
    is_primary = models.BooleanField(default=False)
    assigned_at = models.DateTimeField(auto_now_add=True)
    assigned_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='role_assignments')
    
    class Meta:
        db_table = 'rbac_user_roles'
        unique_together = ['user', 'restaurant', 'role']
        ordering = ['-is_primary', '-assigned_at']
    
    def __str__(self):
        return f"{self.user.email} -> {self.role.get_name_display()} ({self.restaurant.name})"


class UserInvitation(models.Model):
    """Invitation system for bulk/individual user onboarding"""
    
    STATUS_CHOICES = (
        ('PENDING', 'Pending'),
        ('ACCEPTED', 'Accepted'),
        ('REJECTED', 'Rejected'),
        ('EXPIRED', 'Expired'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='user_invitations')
    email = models.EmailField()
    role = models.ForeignKey(Role, on_delete=models.SET_NULL, null=True, blank=True)
    first_name = models.CharField(max_length=100, blank=True, null=True)
    last_name = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    invitation_token = models.CharField(max_length=255, unique=True)
    sent_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(blank=True, null=True)
    accepted_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='invitations_accepted')
    invited_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='invitations_sent')
    is_bulk_invite = models.BooleanField(default=False)
    bulk_batch_id = models.CharField(max_length=50, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'rbac_user_invitations'
        ordering = ['-sent_at']
    
    def __str__(self):
        return f"Invitation to {self.email} for {self.restaurant.name}"
    
    def is_expired(self):
        """Check if invitation has expired"""
        from django.utils import timezone
        return timezone.now() > self.expires_at and self.status == 'PENDING'


class AuditLog(models.Model):
    """Audit trail for all user actions (compliance & debugging)"""
    
    ACTION_TYPES = (
        ('CREATE', 'Created'),
        ('UPDATE', 'Updated'),
        ('DELETE', 'Deleted'),
        ('LOGIN', 'Login'),
        ('LOGOUT', 'Logout'),
        ('PERMISSION_CHANGE', 'Permission Changed'),
        ('ORDER_ACTION', 'Order Action'),
        ('INVENTORY_ACTION', 'Inventory Action'),
        ('OTHER', 'Other'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='audit_logs')
    user = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs')
    action_type = models.CharField(max_length=50, choices=ACTION_TYPES)
    entity_type = models.CharField(max_length=100)
    entity_id = models.CharField(max_length=100, blank=True, null=True)
    description = models.TextField()
    old_values = models.JSONField(default=dict, blank=True)
    new_values = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.TextField(blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'audit_logs'
        ordering = ['-timestamp']
    
    def __str__(self):
        return f"{self.get_action_type_display()} by {self.user.email if self.user else 'Unknown'}"


class StaffProfile(models.Model):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='profile')
    contract_end_date = models.DateField(null=True, blank=True)
    health_card_expiry = models.DateField(null=True, blank=True)
    hourly_rate = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    emergency_contact_name = models.CharField(max_length=255, blank=True, null=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True, null=True)
    notes = models.TextField(blank=True)
    last_location_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_location_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_location_timestamp = models.DateTimeField(null=True, blank=True)
    geofence_alerts_enabled = models.BooleanField(default=True)
    
    def __str__(self):
        return f"Profile - {self.user.email}"


class POSIntegration(models.Model):
    """Track POS transaction history and syncing"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.OneToOneField(Restaurant, on_delete=models.CASCADE, related_name='pos_integration')
    last_sync_time = models.DateTimeField(null=True, blank=True)
    sync_status = models.CharField(max_length=20, choices=[
        ('CONNECTED', 'Connected'),
        ('DISCONNECTED', 'Disconnected'),
        ('ERROR', 'Error'),
        ('SYNCING', 'Syncing'),
    ], default='DISCONNECTED')
    total_transactions_synced = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'pos_integrations'
    
    def __str__(self):
        return f"POS Integration - {self.restaurant.name}"


class AIAssistantConfig(models.Model):
    """AI Assistant configuration per restaurant"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.OneToOneField(Restaurant, on_delete=models.CASCADE, related_name='ai_config')
    enabled = models.BooleanField(default=True)
    ai_provider = models.CharField(max_length=50, choices=[
        ('GROQ', 'Groq'),
        ('OPENAI', 'OpenAI'),
        ('CLAUDE', 'Claude'),
    ], default='GROQ')
    api_key = models.CharField(max_length=500, blank=True, null=True)  # Encrypted in production
    features_enabled = models.JSONField(default=dict)  # e.g., {'insights': True, 'recommendations': True, 'reports': True}
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'ai_assistant_configs'
    
    def __str__(self):
        return f"AI Config - {self.restaurant.name}"