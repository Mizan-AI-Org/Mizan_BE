from rest_framework import permissions

class IsSuperAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'SUPER_ADMIN'

class IsAdminOrSuperAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in ['SUPER_ADMIN', 'ADMIN']

class IsSameRestaurant(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        # For objects that have restaurant field
        if hasattr(obj, 'restaurant'):
            return obj.restaurant == request.user.restaurant
        # For user objects
        elif isinstance(obj, request.user.__class__):
            return obj.restaurant == request.user.restaurant
        return False

class IsAdminOrManager(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in ['SUPER_ADMIN', 'ADMIN', 'MANAGER']