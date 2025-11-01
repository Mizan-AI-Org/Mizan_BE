"""
Role-Based Access Control (RBAC) Models for Multi-Tenant Architecture
"""
from django.db import models
from django.contrib.auth.models import Permission as DjangoPermission
import uuid
from django.utils import timezone
from datetime import timedelta


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
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='roles')
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
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='permissions')
    code = models.CharField(max_length=100, unique=True)  # e.g., 'pos.create_order', 'inventory.restock'
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


class UserInvitation(models.Model):
    """Invitation system for bulk/individual user onboarding"""
    
    STATUS_CHOICES = (
        ('PENDING', 'Pending'),
        ('ACCEPTED', 'Accepted'),
        ('REJECTED', 'Rejected'),
        ('EXPIRED', 'Expired'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='user_invitations')
    email = models.EmailField()
    role = models.ForeignKey(Role, on_delete=models.SET_NULL, null=True, blank=True)
    first_name = models.CharField(max_length=100, blank=True, null=True)
    last_name = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    invitation_token = models.CharField(max_length=255, unique=True)
    sent_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(blank=True, null=True)
    accepted_by = models.ForeignKey('accounts.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='invitations_accepted')
    invited_by = models.ForeignKey('accounts.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='invitations_sent')
    is_bulk_invite = models.BooleanField(default=False)  # True if part of bulk CSV upload
    bulk_batch_id = models.CharField(max_length=50, blank=True, null=True)  # Group bulk invites together
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'rbac_user_invitations'
        ordering = ['-sent_at']
        indexes = [
            models.Index(fields=['restaurant', 'status']),
            models.Index(fields=['invitation_token']),
            models.Index(fields=['email', 'restaurant']),
        ]
    
    def __str__(self):
        return f"Invitation to {self.email} for {self.restaurant.name}"
    
    def is_expired(self):
        """Check if invitation has expired"""
        return timezone.now() > self.expires_at and self.status == 'PENDING'
    
    @staticmethod
    def create_invitation(restaurant, email, role, invited_by, expires_in_days=7, bulk_batch_id=None):
        """Factory method to create an invitation with token"""
        import secrets
        token = secrets.token_urlsafe(32)
        
        invitation = UserInvitation(
            restaurant=restaurant,
            email=email,
            role=role,
            invitation_token=token,
            expires_at=timezone.now() + timedelta(days=expires_in_days),
            invited_by=invited_by,
            bulk_batch_id=bulk_batch_id,
        )
        return invitation


class UserRole(models.Model):
    """Maps users to roles in a restaurant (multi-tenancy support)"""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey('accounts.CustomUser', on_delete=models.CASCADE, related_name='restaurant_roles')
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='user_roles')
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name='users')
    is_primary = models.BooleanField(default=False)  # Primary role if user has multiple roles
    assigned_at = models.DateTimeField(auto_now_add=True)
    assigned_by = models.ForeignKey('accounts.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='role_assignments')
    
    class Meta:
        db_table = 'rbac_user_roles'
        unique_together = ['user', 'restaurant', 'role']
        ordering = ['-is_primary', '-assigned_at']
    
    def __str__(self):
        return f"{self.user.email} -> {self.role.get_name_display()} ({self.restaurant.name})"


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
    restaurant = models.ForeignKey('accounts.Restaurant', on_delete=models.CASCADE, related_name='audit_logs')
    user = models.ForeignKey('accounts.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs')
    action_type = models.CharField(max_length=50, choices=ACTION_TYPES)
    entity_type = models.CharField(max_length=100)  # e.g., 'Order', 'InventoryItem'
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
        indexes = [
            models.Index(fields=['restaurant', 'timestamp']),
            models.Index(fields=['user', 'timestamp']),
            models.Index(fields=['action_type']),
        ]
    
    def __str__(self):
        return f"{self.get_action_type_display()} by {self.user.email if self.user else 'Unknown'}"