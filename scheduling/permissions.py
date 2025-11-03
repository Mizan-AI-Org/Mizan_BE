"""
Custom permissions for the scheduling app
"""
from rest_framework import permissions


class IsManagerOrAdmin(permissions.BasePermission):
    """
    Custom permission to only allow managers or admin users to access certain views.
    """
    
    def has_permission(self, request, view):
        """
        Check if user has manager or admin permissions
        """
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Allow superusers
        if request.user.is_superuser:
            return True
        
        # Check if user has manager role or is staff
        if hasattr(request.user, 'role'):
            return request.user.role in ['MANAGER', 'ADMIN'] or request.user.is_staff
        
        # Fallback to staff status
        return request.user.is_staff


class IsOwnerOrManager(permissions.BasePermission):
    """
    Custom permission to only allow owners of an object or managers to edit it.
    """
    
    def has_object_permission(self, request, view, obj):
        """
        Check if user is owner or has manager permissions
        """
        # Read permissions for any authenticated user
        if request.method in permissions.SAFE_METHODS:
            return request.user.is_authenticated
        
        # Allow superusers
        if request.user.is_superuser:
            return True
        
        # Check if user is the owner
        if hasattr(obj, 'created_by') and obj.created_by == request.user:
            return True
        
        if hasattr(obj, 'staff') and obj.staff == request.user:
            return True
        
        # Check if user has manager role
        if hasattr(request.user, 'role'):
            return request.user.role in ['MANAGER', 'ADMIN'] or request.user.is_staff
        
        # Fallback to staff status
        return request.user.is_staff


class IsRestaurantMember(permissions.BasePermission):
    """
    Custom permission to only allow users who belong to the same restaurant.
    """
    
    def has_permission(self, request, view):
        """
        Check if user is authenticated and belongs to a restaurant
        """
        if not request.user or not request.user.is_authenticated:
            return False
        
        return hasattr(request.user, 'restaurant') and request.user.restaurant is not None
    
    def has_object_permission(self, request, view, obj):
        """
        Check if user belongs to the same restaurant as the object
        """
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Allow superusers
        if request.user.is_superuser:
            return True
        
        # Check if object has restaurant field
        if hasattr(obj, 'restaurant'):
            return obj.restaurant == request.user.restaurant
        
        # Check if object has staff field with restaurant
        if hasattr(obj, 'staff') and hasattr(obj.staff, 'restaurant'):
            return obj.staff.restaurant == request.user.restaurant
        
        # Check if object has schedule field with restaurant
        if hasattr(obj, 'schedule') and hasattr(obj.schedule, 'restaurant'):
            return obj.schedule.restaurant == request.user.restaurant
        
        return False