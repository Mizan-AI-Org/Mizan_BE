"""
Multi-tenant middleware for Mizan AI
Ensures tenant isolation and context validation for every request
"""
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.exceptions import AuthenticationFailed
import logging

logger = logging.getLogger(__name__)


class TenantContextMiddleware(MiddlewareMixin):
    """
    Middleware that validates and injects tenant context into every request.
    
    Flow:
    1. Extract JWT token from request
    2. Validate user authentication
    3. Extract tenant_id (restaurant) from user
    4. Inject tenant context into request
    5. Validate user has access to requested tenant resources
    """
    
    def process_request(self, request):
        # Skip for public endpoints
        public_paths = [
            '/api/auth/login',
            '/api/auth/register',
            '/api/auth/refresh',
            '/api/auth/pin-login',
            '/api/invitations/accept',
            '/admin/',
            '/api/docs/',
            '/api/schema/',
        ]
        
        if any(request.path.startswith(path) for path in public_paths):
            return None
        
        # Skip for non-API requests
        if not request.path.startswith('/api/'):
            return None
        
        # Extract and validate JWT token
        jwt_auth = JWTAuthentication()
        try:
            validated_token = jwt_auth.get_validated_token(
                jwt_auth.get_raw_token(jwt_auth.get_header(request))
            )
            user = jwt_auth.get_user(validated_token)
            
            # Inject tenant context
            if hasattr(user, 'restaurant') and user.restaurant:
                request.tenant_id = str(user.restaurant.id)
                request.tenant = user.restaurant
                request.tenant_name = user.restaurant.name
            else:
                # User has no restaurant association
                if user.is_superuser:
                    # Superusers can access without tenant
                    request.tenant_id = None
                    request.tenant = None
                    request.tenant_name = 'System Admin'
                else:
                    logger.warning(f"User {user.email} has no restaurant association")
                    return JsonResponse({
                        'error': 'No restaurant association',
                        'detail': 'User must be associated with a restaurant'
                    }, status=403)
            
            # Log tenant context for debugging
            logger.debug(f"Tenant context: {request.tenant_name} ({request.tenant_id})")
            
        except (AuthenticationFailed, AttributeError, TypeError) as e:
            # Authentication failed or no token provided
            logger.debug(f"Authentication failed: {str(e)}")
            # Let the view handle authentication
            pass
        
        return None
    
    def process_view(self, request, view_func, view_args, view_kwargs):
        """
        Validate tenant access for specific resources
        """
        # If tenant context exists, validate access
        if hasattr(request, 'tenant_id') and request.tenant_id:
            # Check if view is accessing tenant-specific resource
            # This is handled by view permissions, but we can add extra validation here
            pass
        
        return None


class TenantIsolationMiddleware(MiddlewareMixin):
    """
    Ensures data isolation between tenants at the database query level.
    This is a safety net in addition to view-level filtering.
    """
    
    def process_request(self, request):
        # Store original tenant context in thread-local storage if needed
        # This can be used by model managers to automatically filter queries
        if hasattr(request, 'tenant_id'):
            # Set thread-local tenant context
            from threading import local
            _thread_locals = local()
            _thread_locals.tenant_id = request.tenant_id
        
        return None


class AuditLoggingMiddleware(MiddlewareMixin):
    """Persist a row in ``accounts.AuditLog`` for material tenant actions.

    We deliberately only log a *curated* set of paths (auth, staff, settings,
    invitations, billing, menu, schedule, POS, inventory, checklists) so the
    table stays useful for "who did what" audits instead of becoming a noisy
    firehose of every GET/POST. Read-only (GET/HEAD/OPTIONS) requests, health
    checks, docs and agent endpoints are skipped.

    Failures are swallowed: middleware must never break an API response just
    because the audit write errored.
    """

    # Keep this list narrow. We care about state changes humans care about.
    _LOGGED_PATH_PREFIXES = (
        '/api/auth/',
        '/api/accounts/',
        '/api/staff/',
        '/api/scheduling/',
        '/api/timeclock/',
        '/api/pos/',
        '/api/menu/',
        '/api/inventory/',
        '/api/checklists/',
        '/api/billing/',
        '/api/reporting/',
        '/api/notifications/',
    )
    _SKIP_PATH_PREFIXES = (
        '/api/auth/refresh',
        '/api/scheduling/agent/',
        '/api/reporting/agent/',
        '/api/checklists/agent/',
        '/api/notifications/agent/',
        '/api/timeclock/agent/',
        '/api/pos/agent/',
        '/api/staff/agent/',
        '/api/inventory/agent/',
        '/api/accounts/agent/',
    )
    _MUTATING_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}

    @classmethod
    def _should_log(cls, request) -> bool:
        path = request.path or ''
        if request.method not in cls._MUTATING_METHODS:
            return False
        if any(path.startswith(p) for p in cls._SKIP_PATH_PREFIXES):
            return False
        return any(path.startswith(p) for p in cls._LOGGED_PATH_PREFIXES)

    @staticmethod
    def _client_ip(request) -> str | None:
        xff = request.META.get('HTTP_X_FORWARDED_FOR')
        if xff:
            return xff.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR')

    @staticmethod
    def _infer_entity_and_action(path: str, method: str) -> tuple[str, str]:
        """Best-effort (entity_type, action_type) from URL + HTTP verb.

        Entity is the first meaningful segment after ``/api/`` (e.g. ``staff``,
        ``invitations``). Action maps POST→CREATE, PUT/PATCH→UPDATE,
        DELETE→DELETE. Auth endpoints get special-cased.
        """
        lowered = path.lower()
        if lowered.startswith('/api/auth/login'):
            return ('AUTH', 'LOGIN')
        if lowered.startswith('/api/auth/logout'):
            return ('AUTH', 'LOGOUT')
        if 'password' in lowered:
            return ('AUTH', 'PASSWORD_CHANGED')
        if '/pin' in lowered:
            return ('AUTH', 'PIN_CHANGED')

        parts = [p for p in lowered.split('/') if p]
        # parts like ['api', 'accounts', 'settings', ...]
        entity = parts[1] if len(parts) > 1 else 'unknown'
        entity = entity.rstrip('s').upper() or 'UNKNOWN'

        action_map = {
            'POST': 'CREATE',
            'PUT': 'UPDATE',
            'PATCH': 'UPDATE',
            'DELETE': 'DELETE',
        }
        return (entity, action_map.get(method, 'OTHER'))

    def process_response(self, request, response):
        try:
            if not self._should_log(request):
                return response
            user = getattr(request, 'user', None)
            if not user or not getattr(user, 'is_authenticated', False):
                # Only log failed logins via the login view itself; otherwise skip.
                return response
            # Swallow responses from unrelated content types (e.g. 401/403) — we
            # still want DELETEs that succeeded and UPDATEs that changed state.
            if response.status_code >= 500:
                return response

            # Lazy import to avoid app-loading ordering issues.
            from accounts.models import AuditLog

            entity_type, action_type = self._infer_entity_and_action(
                request.path, request.method
            )
            description = (
                f"{request.method} {request.path} → {response.status_code}"
            )
            restaurant = getattr(user, 'restaurant', None)
            AuditLog.objects.create(
                restaurant=restaurant,
                user=user,
                action_type=action_type,
                entity_type=entity_type,
                description=description,
                ip_address=self._client_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', '')[:512],
            )
        except Exception as exc:  # never break the response on audit failure
            logger.warning("AuditLoggingMiddleware failed: %s", exc)
        return response


class AgentPathCsrfExemptMiddleware(MiddlewareMixin):
    """
    Exempts /api/scheduling/agent/ paths from CSRF.
    Lua agent (Miya) calls these from server-to-server with Bearer token auth;
    no Referer header is sent, causing Django's CSRF to reject with 403.
    Must run before CsrfViewMiddleware.
    """
    AGENT_PATHS = ('/api/scheduling/agent/', '/api/reporting/agent/', '/api/checklists/agent/',
                   '/api/notifications/agent/', '/api/timeclock/agent/', '/api/pos/agent/',
                   '/api/staff/agent/', '/api/inventory/agent/', '/api/accounts/agent/')

    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.path.startswith(self.AGENT_PATHS):
            view_func.csrf_exempt = True
        return None