"""
Tenant Context Middleware for Multi-Tenant Architecture

This middleware extracts tenant context from JWT tokens and validates
user permissions for the requested tenant.
"""
import logging
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareNotUsed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.exceptions import AuthenticationFailed
from accounts.models import Restaurant, UserRole
from .utils import get_tenant_from_request

logger = logging.getLogger(__name__)


class TenantContextMiddleware:
    """
    Middleware that:
    1. Extracts tenant_id from JWT token or request headers
    2. Validates user has access to the tenant
    3. Injects tenant context into request
    4. Prevents cross-tenant data access
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        # Endpoints that don't require tenant context
        self.excluded_paths = [
            '/api/auth/login',
            '/api/auth/register',
            '/api/auth/token/refresh',
            '/api/auth/reset-password',
            '/api/health',
            '/admin',
        ]
    
    def __call__(self, request):
        # Skip middleware for excluded paths
        if any(request.path.startswith(path) for path in self.excluded_paths):
            return self.get_response(request)
        
        # Extract tenant context
        try:
            tenant = get_tenant_from_request(request)
            request.tenant = tenant
            request.tenant_id = tenant.id if tenant else None
        except Exception as e:
            logger.warning(f"Tenant extraction failed: {str(e)}")
            request.tenant = None
            request.tenant_id = None
        
        # Validate user access to tenant (if authenticated)
        if request.user and request.user.is_authenticated and request.tenant:
            try:
                # Check if user belongs to this restaurant
                user_restaurant_access = UserRole.objects.filter(
                    user=request.user,
                    restaurant=request.tenant
                ).exists()
                
                if not user_restaurant_access and not request.user.is_superuser:
                    return JsonResponse(
                        {'error': 'Access denied to this restaurant'},
                        status=403
                    )
            except Exception as e:
                logger.error(f"Tenant validation failed: {str(e)}")
                return JsonResponse(
                    {'error': 'Tenant validation error'},
                    status=500
                )
        
        response = self.get_response(request)
        return response


class TenantIsolationMixin:
    """
    Mixin for ViewSets to enforce tenant isolation.
    Automatically filters querysets by request.tenant
    """
    
    def get_queryset(self):
        """Override queryset to filter by tenant"""
        queryset = super().get_queryset()
        
        if not self.request.tenant:
            return queryset.none()  # Return empty if no tenant
        
        # Filter by restaurant field (adjust field name if different)
        return queryset.filter(restaurant=self.request.tenant)
    
    def perform_create(self, serializer):
        """Automatically set restaurant to current tenant"""
        serializer.save(restaurant=self.request.tenant)
    
    def perform_update(self, serializer):
        """Ensure updated object belongs to tenant"""
        instance = serializer.instance
        if hasattr(instance, 'restaurant') and instance.restaurant != self.request.tenant:
            raise PermissionError("Cannot update object from another restaurant")
        serializer.save()


class PermissionCheckMixin:
    """
    Mixin for ViewSets to check user permissions against role-based access control
    """
    
    required_permission = None  # Override with permission code like 'pos.create_order'
    
    def check_permissions(self, request):
        """Check if user has required permission for action"""
        super().check_permissions(request)
        
        if not self.required_permission:
            return  # No specific permission check needed
        
        if not self._user_has_permission(request.user, self.required_permission):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied(
                f"User does not have permission: {self.required_permission}"
            )
    
    def _user_has_permission(self, user, permission_code):
        """Check if user has specific permission in current tenant"""
        if user.is_superuser:
            return True
        
        try:
            # Get user's roles in current tenant
            user_roles = UserRole.objects.filter(
                user=user,
                restaurant=self.request.tenant
            ).select_related('role')
            
            # Check if any role has the permission
            from accounts.models_rbac import RolePermission, Permission
            
            for user_role in user_roles:
                has_perm = RolePermission.objects.filter(
                    role=user_role.role,
                    permission__code=permission_code,
                    permission__is_active=True
                ).exists()
                
                if has_perm:
                    return True
            
            return False
        except Exception as e:
            logger.error(f"Permission check failed: {str(e)}")
            return False


class AuditLoggingMixin:
    """
    Mixin to log all user actions to AuditLog
    """
    
    def perform_create(self, serializer):
        """Log creation"""
        instance = super().perform_create(serializer)
        self._log_audit('CREATE', serializer.instance)
        return instance
    
    def perform_update(self, serializer):
        """Log updates"""
        super().perform_update(serializer)
        self._log_audit('UPDATE', serializer.instance)
    
    def perform_destroy(self, instance):
        """Log deletions"""
        self._log_audit('DELETE', instance)
        super().perform_destroy(instance)
    
    def _log_audit(self, action_type, instance):
        """Create audit log entry"""
        try:
            from accounts.models_rbac import AuditLog
            
            AuditLog.objects.create(
                restaurant=self.request.tenant,
                user=self.request.user,
                action_type=action_type,
                entity_type=instance.__class__.__name__,
                entity_id=str(instance.id),
                description=f"{action_type} {instance.__class__.__name__}",
                ip_address=self._get_client_ip(),
                user_agent=self.request.META.get('HTTP_USER_AGENT', ''),
            )
        except Exception as e:
            logger.error(f"Audit logging failed: {str(e)}")
    
    def _get_client_ip(self):
        """Extract client IP from request"""
        x_forwarded_for = self.request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return self.request.META.get('REMOTE_ADDR')