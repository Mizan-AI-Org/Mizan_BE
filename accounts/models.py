from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError
import uuid, re
from django.contrib.auth.hashers import make_password, check_password
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from core.crypto import encrypt_json, decrypt_json

class CustomUserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)
        
        # Get the pin_code from extra_fields, if it exists
        pin_code = extra_fields.pop('pin_code', None)
        
        is_verified = extra_fields.pop('is_verified', False) 
        user = self.model(email=email, is_verified=is_verified, **extra_fields)

        if password:
            # For superusers or owners who use a password
            user.set_password(password)
        elif pin_code:
            # For staff who use a PIN
            user.set_pin(pin_code)
            user.set_unusable_password() # This is the magic part!
        else:
            # No password or PIN provided
            raise ValueError('A password or a pin_code is required to create a user.')

        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        # create_user with a password, so the superuser
        # will have a password and not a PIN.
        
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
    address = models.CharField(max_length=255, blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(unique=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    radius = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True, default=100, validators=[
        MinValueValidator(5),
        MaxValueValidator(100)
    ])  # Geofence radius in meters (5m to 100m range)
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
    general_settings = models.JSONField(default=dict)
    settings_schema_version = models.IntegerField(default=1)
    
    # POS Integration Fields
    pos_provider = models.CharField(max_length=50, choices=[
        ('STRIPE', 'Stripe'),
        ('SQUARE', 'Square'),
        ('TOAST', 'Toast'),
        ('LIGHTSPEED', 'Lightspeed'),
        ('CLOVER', 'Clover'),
        ('CUSTOM', 'Custom API'),
        ('NONE', 'Not Configured')
    ], default='NONE')
    pos_merchant_id = models.CharField(max_length=255, blank=True, null=True)
    pos_api_key = models.CharField(max_length=255, blank=True, null=True)
    pos_is_connected = models.BooleanField(default=False)
    # OAuth-based POS credentials (encrypted). Stored as JSON; currently used for Square.
    pos_oauth_data = models.TextField(blank=True, null=True)
    # Provider location identifier (e.g. Square location_id)
    pos_location_id = models.CharField(max_length=255, blank=True, null=True)
    # Access token expiry for OAuth providers (best-effort; source of truth is provider)
    pos_token_expires_at = models.DateTimeField(blank=True, null=True)
    
    class Meta:
        db_table = 'restaurants'
    
    def __str__(self):
        return self.name

    def get_pos_oauth(self) -> dict:
        """Return decrypted POS OAuth payload dict (or {})."""
        if not self.pos_oauth_data:
            return {}
        try:
            return decrypt_json(self.pos_oauth_data)
        except Exception:
            return {}

    def set_pos_oauth(self, payload: dict) -> None:
        """Encrypt and store POS OAuth payload dict."""
        self.pos_oauth_data = encrypt_json(payload or {})

    # --- Square helpers (provider-specific) ---
    def get_square_oauth(self) -> dict:
        return (self.get_pos_oauth() or {}).get("square", {}) or {}

    def set_square_oauth(self, square_payload: dict) -> None:
        root = self.get_pos_oauth() or {}
        root["square"] = square_payload or {}
        self.set_pos_oauth(root)

    def get_square_access_token(self) -> str:
        """Prefer OAuth access token; fall back to legacy pos_api_key."""
        sq = self.get_square_oauth()
        token = sq.get("access_token") or ""
        return token or (self.pos_api_key or "")

    def get_square_refresh_token(self) -> str:
        sq = self.get_square_oauth()
        return sq.get("refresh_token") or ""

class CustomUser(AbstractUser):
    ROLE_CHOICES = settings.STAFF_ROLES_CHOICES
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pin_code = models.CharField(max_length=255, unique=True, blank=True, null=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    phone = models.CharField(max_length=20, blank=True, null=True)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='staff', null=True, blank=True)
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Security fields for account lockout
    failed_login_attempts = models.IntegerField(default=0)
    account_locked_until = models.DateTimeField(null=True, blank=True)
    last_failed_login = models.DateTimeField(null=True, blank=True)
    last_successful_login = models.DateTimeField(null=True, blank=True)
    
    # Password reset fields
    password_reset_token = models.CharField(max_length=64, blank=True, null=True)
    password_reset_expires = models.DateTimeField(blank=True, null=True)

    # Language preference (optional; if unset, fall back to restaurant.language)
    preferred_language = models.CharField(
        max_length=10,
        choices=[
            ('en', 'English'),
            ('fr', 'French'),
            ('ar', 'Arabic'),
        ],
        blank=True,
        null=True,
    )

    
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
        """Set a 4-digit PIN for staff users with validation."""
        if not self.is_staff_role():
            raise ValidationError("Only staff members can have PIN codes.")
        
        # Validate PIN format
        if not re.match(r'^\d{4}$', str(raw_pin)):
            raise ValidationError("PIN must be exactly 4 digits.")
        
        self.pin_code = make_password(str(raw_pin))
        
    def check_pin(self, raw_pin):
        """Check PIN with account lockout protection."""
        if self.is_account_locked():
            return False
            
        if not self.pin_code:
            return False
            
        is_valid = check_password(str(raw_pin), self.pin_code)
        
        if is_valid:
            self.reset_failed_attempts()
            self.last_successful_login = timezone.now()
            self.save(update_fields=['failed_login_attempts', 'account_locked_until', 'last_successful_login'])
        else:
            self.increment_failed_attempts()
            
        return is_valid
    
    def is_staff_role(self):
        """Check if user has a staff role (not admin/owner)."""
        admin_roles = ['SUPER_ADMIN', 'ADMIN', 'OWNER', 'MANAGER']
        return self.role not in admin_roles
    
    def is_admin_role(self):
        """Check if user has an admin role."""
        admin_roles = ['SUPER_ADMIN', 'ADMIN', 'OWNER', 'MANAGER']
        return self.role in admin_roles
    
    def is_account_locked(self):
        """Check if account is currently locked."""
        if not self.account_locked_until:
            return False
        return timezone.now() < self.account_locked_until
    
    def increment_failed_attempts(self):
        """Increment failed login attempts and lock account if necessary."""
        self.failed_login_attempts += 1
        self.last_failed_login = timezone.now()
        
        # Lock account after 5 failed attempts
        if self.failed_login_attempts >= 5:
            # Lock for 30 minutes
            self.account_locked_until = timezone.now() + timedelta(minutes=30)
            
        self.save(update_fields=['failed_login_attempts', 'last_failed_login', 'account_locked_until'])
    
    def reset_failed_attempts(self):
        """Reset failed login attempts after successful login."""
        self.failed_login_attempts = 0
        self.account_locked_until = None
        
    def validate_password_complexity(self, password):
        """Validate password complexity for admin users."""
        if not self.is_admin_role():
            return True
            
        if len(password) < 8:
            raise ValidationError("Password must be at least 8 characters long.")
        
        if not re.search(r'[A-Z]', password):
            raise ValidationError("Password must contain at least one uppercase letter.")
        
        if not re.search(r'[a-z]', password):
            raise ValidationError("Password must contain at least one lowercase letter.")
        
        if not re.search(r'\d', password):
            raise ValidationError("Password must contain at least one digit.")
        
        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
            raise ValidationError("Password must contain at least one special character.")
        
        return True
    
    def generate_password_reset_token(self):
        """Generate a secure password reset token with 1-hour expiry."""
        from django.utils.crypto import get_random_string
        self.password_reset_token = get_random_string(64)
        self.password_reset_expires = timezone.now() + timedelta(hours=1)
        self.save(update_fields=['password_reset_token', 'password_reset_expires'])
        return self.password_reset_token
    
    def validate_password_reset_token(self, token):
        """Validate the password reset token."""
        if not self.password_reset_token or not self.password_reset_expires:
            return False
        if self.password_reset_token != token:
            return False
        if timezone.now() > self.password_reset_expires:
            return False
        return True
    
    def clear_password_reset_token(self):
        """Clear the password reset token after use."""
        self.password_reset_token = None
        self.password_reset_expires = None
        self.save(update_fields=['password_reset_token', 'password_reset_expires'])

class StaffInvitation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(blank=True, null=True)  # Made optional for phone-only invitations
    role = models.CharField(max_length=20, choices=CustomUser.ROLE_CHOICES)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE)
    invited_by = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    invitation_token = models.CharField(max_length=100, unique=True)
    is_accepted = models.BooleanField(default=False)
    sent_at = models.DateTimeField(auto_now_add=True)  # Renamed from created_at for clarity
    expires_at = models.DateTimeField()
    # Store optional data: first_name, last_name, department, phone, phone_number
    extra_data = models.JSONField(default=dict, blank=True)
    
    class Meta:
        db_table = 'staff_invitations'
    
    def clean(self):
        """Ensure at least email or phone is provided"""
        from django.core.exceptions import ValidationError
        phone = self.extra_data.get('phone') or self.extra_data.get('phone_number')
        if not self.email and not phone:
            raise ValidationError('Either email or phone number must be provided')
    
    @property
    def first_name(self):
        return self.extra_data.get('first_name', '')
    
    @property
    def last_name(self):
        return self.extra_data.get('last_name', '')



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
    email = models.EmailField(blank=True, null=True)
    role = models.CharField(max_length=20, choices=CustomUser.ROLE_CHOICES)
    first_name = models.CharField(max_length=100, blank=True, null=True)
    last_name = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    invitation_token = models.CharField(max_length=255, unique=True)
    sent_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    # optional onboarding details (e.g., phone_number, department)
    extra_data = models.JSONField(default=dict, blank=True)
    is_accepted = models.BooleanField(default=False)
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

    @classmethod
    def create_invitation(cls, restaurant, email, role, invited_by, expires_in_days=7, bulk_batch_id=None):
        """Factory method to create an invitation with token"""
        import secrets
        from django.utils.crypto import get_random_string
        token = get_random_string(64)
        
        invitation = cls.objects.create(
            restaurant=restaurant,
            email=email,
            role=role,
            invitation_token=token,
            expires_at=timezone.now() + timedelta(days=expires_in_days),
            invited_by=invited_by,
            bulk_batch_id=bulk_batch_id,
        )
        return invitation




class AuditLog(models.Model):
    """Audit trail for all user actions (compliance & debugging)"""
    
    ACTION_TYPES = (
        ('CREATE', 'Created'),
        ('UPDATE', 'Updated'),
        ('DELETE', 'Deleted'),
        ('LOGIN', 'Login'),
        ('LOGIN_FAILED', 'Login Failed'),
        ('LOGIN_PIN', 'PIN Login'),
        ('LOGIN_PIN_FAILED', 'PIN Login Failed'),
        ('LOGOUT', 'Logout'),
        ('ACCOUNT_LOCKED', 'Account Locked'),
        ('ACCOUNT_UNLOCKED', 'Account Unlocked'),
        ('PASSWORD_CHANGED', 'Password Changed'),
        ('PIN_CHANGED', 'PIN Changed'),
        ('PERMISSION_CHANGE', 'Permission Changed'),
        ('ORDER_ACTION', 'Order Action'),
        ('INVENTORY_ACTION', 'Inventory Action'),
        ('OTHER', 'Other'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='audit_logs', null=True, blank=True)
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
    
    @classmethod
    def create_log(cls, restaurant, user, action_type, entity_type, description, 
                   entity_id=None, old_values=None, new_values=None, 
                   ip_address=None, user_agent=None):
        """Create an audit log entry."""
        return cls.objects.create(
            restaurant=restaurant,
            user=user,
            action_type=action_type,
            entity_type=entity_type,
            entity_id=entity_id,
            description=description,
            old_values=old_values or {},
            new_values=new_values or {},
            ip_address=ip_address,
            user_agent=user_agent
        )


class StaffProfile(models.Model):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='profile')
    contract_end_date = models.DateField(null=True, blank=True)
    health_card_expiry = models.DateField(null=True, blank=True)
    hourly_rate = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    salary_type = models.CharField(max_length=20, choices=[('HOURLY', 'Hourly'), ('MONTHLY', 'Monthly')], default='HOURLY')
    join_date = models.DateField(null=True, blank=True)
    promotion_history = models.JSONField(default=list, blank=True)
    emergency_contact_name = models.CharField(max_length=255, blank=True, null=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True, null=True)
    notes = models.TextField(blank=True)
    last_location_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_location_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_location_timestamp = models.DateTimeField(null=True, blank=True)
    geofence_alerts_enabled = models.BooleanField(default=True)
    # Optional department info captured during onboarding
    department = models.CharField(max_length=100, blank=True, null=True)
    
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


class InvitationDeliveryLog(models.Model):
    """Log of all invitation delivery attempts"""
    STATUS_CHOICES = (
        ('PENDING', 'Pending'),
        ('SENT', 'Sent'),
        ('DELIVERED', 'Delivered'),
        ('READ', 'Read'),
        ('FAILED', 'Failed'),
    )

    invitation = models.ForeignKey(UserInvitation, on_delete=models.CASCADE, related_name='delivery_logs')
    channel = models.CharField(max_length=20) # 'email', 'whatsapp'
    recipient_address = models.CharField(max_length=255) # email or phone
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    external_id = models.CharField(max_length=255, blank=True, null=True, help_text="External message ID (e.g. wamid)")
    response_data = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, null=True)
    attempt_count = models.IntegerField(default=1)
    
    sent_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'invitation_delivery_logs'
        ordering = ['-sent_at']

    def __str__(self):
        return f"{self.channel} invitation to {self.recipient_address} - {self.status}"
    
