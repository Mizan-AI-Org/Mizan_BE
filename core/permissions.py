"""
Custom permission classes for Mizan AI
"""
from rest_framework import permissions


class IsRestaurantOwnerOrManager(permissions.BasePermission):
    """
    Custom permission to check if user is restaurant owner or manager
    """
    def has_object_permission(self, request, view, obj):
        if not hasattr(request.user, 'restaurant'):
            return False
        
        # Get restaurant from object
        restaurant = None
        if hasattr(obj, 'restaurant'):
            restaurant = obj.restaurant
        
        if restaurant is None:
            return False
        
        # Check if user belongs to the restaurant
        if request.user.restaurant != restaurant:
            return False
        
        # Check if user has appropriate role
        allowed_roles = ['SUPER_ADMIN', 'ADMIN', 'MANAGER', 'OWNER']
        return request.user.role in allowed_roles


class IsOwnerOrSuperAdmin(permissions.BasePermission):
    """
    Only Restaurant Owner and Super Admin. Use for destructive or sensitive actions
    (e.g. deactivate staff, delete staff).
    """
    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role in ['OWNER', 'SUPER_ADMIN']
        )


class IsRestaurantStaff(permissions.BasePermission):
    """
    Custom permission to check if user is restaurant staff
    """
    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            hasattr(request.user, 'restaurant') and
            request.user.restaurant
        )


class IsRestaurantOwner(permissions.BasePermission):
    """
    Custom permission to check if user is restaurant owner (SUPER_ADMIN)
    """
    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            request.user.role == 'SUPER_ADMIN'
        )


class IsManager(permissions.BasePermission):
    """
    Custom permission to check if user is a manager
    """
    def has_permission(self, request, view):
        allowed_roles = ['SUPER_ADMIN', 'ADMIN']
        return bool(
            request.user and
            request.user.is_authenticated and
            request.user.role in allowed_roles
        )


class ReadOnly(permissions.BasePermission):
    """
    Allow read-only access (GET, HEAD, OPTIONS requests)
    """
    def has_permission(self, request, view):
        return request.method in permissions.SAFE_METHODS