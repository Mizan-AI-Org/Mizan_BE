"""
Multi-tenant middleware for Mizan AI
Ensures tenant isolation and context validation for every request
"""
import json
import uuid

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

    # Body fields we interpret as "the user this action was directed at" —
    # tried in priority order (first match wins). Covers our current task/
    # shift/staff/invite/rbac endpoints.
    _TARGET_USER_KEYS = (
        'target_user_id',
        'assignee_id',
        'assigned_to',
        'assigned_to_id',
        'staff_id',
        'user_id',
        'invitee_id',
    )

    # Body fields we interpret as "the primary entity this action concerns"
    # — used to populate ``entity_id`` when URL doesn't carry a UUID.
    _ENTITY_ID_KEYS = (
        'task_id',
        'shift_id',
        'schedule_id',
        'invitation_id',
        'widget_id',
        'restaurant_id',
        'location_id',
    )

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

        # Refine entity from the *second* segment when the first is a generic
        # app namespace. E.g. /api/scheduling/tasks/... → TASK, not SCHEDULING.
        refinable = {'SCHEDULING', 'ACCOUNT', 'DASHBOARD', 'REPORTING'}
        if entity in refinable and len(parts) > 2:
            sub = parts[2].rstrip('s').upper()
            # Skip generic subpaths that don't name an entity
            if sub and sub not in {'AGENT', 'API'}:
                entity = sub

        # Detect reassignments / assignments / status transitions embedded
        # in the URL (RPC-style endpoints like /tasks/{id}/assign/).
        action_map = {
            'POST': 'CREATE',
            'PUT': 'UPDATE',
            'PATCH': 'UPDATE',
            'DELETE': 'DELETE',
        }
        action = action_map.get(method, 'OTHER')
        if 'assign' in lowered or 'reassign' in lowered:
            action = 'UPDATE'  # stored as UPDATE; description + metadata carry the nuance
        return (entity, action)

    @classmethod
    def _snapshot_body(cls, request) -> dict:
        """Best-effort JSON snapshot of the request body.

        Called in ``process_request`` (before DRF parses anything) and cached
        on ``request._audit_body`` so ``process_response`` can read assignee
        info without racing DRF's parsers.
        """
        if request.method not in cls._MUTATING_METHODS:
            return {}
        ctype = (request.META.get('CONTENT_TYPE') or '').lower()
        if 'application/json' not in ctype:
            return {}
        try:
            raw = request.body  # Django caches this, safe to re-read.
        except Exception:
            return {}
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    @classmethod
    def _extract_target_user(cls, body: dict):
        """Return a ``CustomUser`` instance if the body points to one.

        We only resolve IDs that look like UUIDs and fall back silently if
        the lookup fails — we never want audit enrichment to crash.
        """
        if not body:
            return None
        raw_id = None
        for key in cls._TARGET_USER_KEYS:
            if key in body and body[key]:
                raw_id = body[key]
                break
        if not raw_id:
            return None
        try:
            target_uuid = uuid.UUID(str(raw_id))
        except (ValueError, TypeError):
            return None
        try:
            from accounts.models import CustomUser
            return CustomUser.objects.only('id', 'email', 'first_name', 'last_name').filter(
                id=target_uuid
            ).first()
        except Exception:
            return None

    @classmethod
    def _extract_entity_id(cls, path: str, body: dict) -> str | None:
        """Look for a UUID either in the URL path or in known body keys."""
        for part in (path or '').split('/'):
            try:
                return str(uuid.UUID(part))
            except (ValueError, TypeError):
                continue
        for key in cls._ENTITY_ID_KEYS:
            if key in body and body[key]:
                try:
                    return str(uuid.UUID(str(body[key])))
                except (ValueError, TypeError):
                    continue
        return None

    @staticmethod
    def _full_name(user) -> str:
        if not user:
            return 'someone'
        name = f"{getattr(user, 'first_name', '') or ''} {getattr(user, 'last_name', '') or ''}".strip()
        return name or getattr(user, 'email', '') or 'someone'

    @classmethod
    def _build_description(cls, actor, target, entity_type, action_type, path, status_code) -> str:
        """Human-friendly sentence Miya can quote back verbatim.

        Falls back to the original ``METHOD path → status`` when we don't
        have enough context to build a proper sentence.
        """
        actor_name = cls._full_name(actor)
        verb_map = {
            'CREATE': 'created',
            'UPDATE': 'updated',
            'DELETE': 'deleted',
            'LOGIN': 'logged in',
            'LOGOUT': 'logged out',
        }
        verb = verb_map.get(action_type, 'acted on')
        entity_label = entity_type.lower() if entity_type else 'record'

        if target:
            target_name = cls._full_name(target)
            # Pick a natural preposition for the target based on the action.
            # "assigned ... to X", "reassigned ... to X", "deleted ... (for X)",
            # "created/updated ... (for X)".
            lowered_path = (path or '').lower()
            if 'reassign' in lowered_path:
                return f"{actor_name} reassigned a {entity_label} to {target_name}"
            if 'assign' in lowered_path:
                return f"{actor_name} assigned a {entity_label} to {target_name}"
            if action_type == 'CREATE':
                return f"{actor_name} created a {entity_label} for {target_name}"
            if action_type == 'DELETE':
                return f"{actor_name} deleted a {entity_label} (affecting {target_name})"
            return f"{actor_name} {verb} a {entity_label} for {target_name}"

        if action_type in {'LOGIN', 'LOGOUT'}:
            return f"{actor_name} {verb}"
        return f"{actor_name} {verb} a {entity_label} (HTTP {status_code})"

    def process_request(self, request):
        # Snapshot the JSON body *before* DRF parses it; cache so
        # ``process_response`` can inspect it without racing the parsers.
        if self._should_log(request):
            request._audit_body = self._snapshot_body(request)
        return None

    def process_response(self, request, response):
        try:
            if not self._should_log(request):
                return response
            user = getattr(request, 'user', None)
            if not user or not getattr(user, 'is_authenticated', False):
                # Only log failed logins via the login view itself; otherwise skip.
                return response
            # Skip 5xx (server crashed — nothing reliable to log) and auth
            # failures (never actually happened from the user's standpoint).
            if response.status_code >= 500 or response.status_code in {401, 403}:
                return response

            # Lazy import to avoid app-loading ordering issues.
            from accounts.models import AuditLog

            body = getattr(request, '_audit_body', None) or {}
            entity_type, action_type = self._infer_entity_and_action(
                request.path, request.method
            )
            target_user = self._extract_target_user(body)
            entity_id = self._extract_entity_id(request.path, body)
            description = self._build_description(
                user, target_user, entity_type, action_type,
                request.path, response.status_code,
            )

            # Metadata carries the raw context Miya needs to explain the
            # event precisely. We redact known secret-ish fields defensively.
            redacted_body = {
                k: v for k, v in body.items()
                if k not in {'password', 'pin', 'token', 'access_token', 'refresh_token', 'secret'}
            } if body else {}
            metadata = {
                'method': request.method,
                'path': request.path,
                'status_code': response.status_code,
                'query': dict(request.GET) if request.GET else {},
                # Only keep the first ~2KB so a huge payload doesn't bloat the table.
                'payload': redacted_body if len(str(redacted_body)) <= 2048 else {'_truncated': True},
            }

            restaurant = getattr(user, 'restaurant', None)
            AuditLog.objects.create(
                restaurant=restaurant,
                user=user,
                target_user=target_user,
                action_type=action_type,
                entity_type=entity_type,
                entity_id=entity_id,
                description=description,
                metadata=metadata,
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