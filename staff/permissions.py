from rest_framework import permissions

class IsManagerOrReadOnly(permissions.BasePermission):
    """
    Custom permission to only allow managers to edit objects.
    Read-only access is allowed for authenticated users.
    """
    
    def has_permission(self, request, view):
        # Read permissions are allowed for any authenticated request
        if request.method in permissions.SAFE_METHODS:
            return request.user.is_authenticated
        
        # Write permissions are only allowed for managers
        return request.user.is_authenticated and (
            request.user.is_staff or 
            request.user.is_superuser or 
            getattr(request.user, 'is_manager', False)
        )

class IsStaffMember(permissions.BasePermission):
    """
    Custom permission to allow staff members to access their own data.
    """
    
    def has_permission(self, request, view):
        return request.user.is_authenticated
    
    def has_object_permission(self, request, view, obj):
        # Check if the user is a manager or admin
        if request.user.is_staff or request.user.is_superuser:
            return True
        
        # Check if the object has a staff field that matches the user
        if hasattr(obj, 'staff'):
            return obj.staff == request.user
        
        # Check if the object has a schedule field with a staff field that matches the user
        if hasattr(obj, 'schedule') and hasattr(obj.schedule, 'staff'):
            return obj.schedule.staff == request.user
        
        return False