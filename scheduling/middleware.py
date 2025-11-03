"""
Middleware for automatic audit context management
Sets user and request context for audit logging
"""

from django.utils.deprecation import MiddlewareMixin
from django.contrib.auth import get_user_model
from .signals import set_current_user, set_current_request, clear_audit_context
from .audit import AuditTrailService, AuditActionType, AuditSeverity

User = get_user_model()

class AuditMiddleware(MiddlewareMixin):
    """
    Middleware to automatically set audit context for each request
    This enables automatic user and request tracking in audit logs
    """
    
    def process_request(self, request):
        """Set audit context at the beginning of each request"""
        # Set current user for audit logging
        if hasattr(request, 'user') and request.user.is_authenticated:
            set_current_user(request.user)
        else:
            set_current_user(None)
        
        # Set current request for audit logging
        set_current_request(request)
        
        # Log user login if this is a login request
        if (request.path.endswith('/login/') or 
            request.path.endswith('/api/auth/login/') or
            request.path.endswith('/api/token/')) and request.method == 'POST':
            # We'll log the login in process_response if successful
            request._is_login_attempt = True
        
        return None
    
    def process_response(self, request, response):
        """Clean up audit context and log session activities"""
        try:
            # Log successful login
            if (hasattr(request, '_is_login_attempt') and 
                request._is_login_attempt and 
                response.status_code in [200, 201] and
                hasattr(request, 'user') and 
                request.user.is_authenticated):
                
                AuditTrailService.log_user_activity(
                    user=request.user,
                    action=AuditActionType.LOGIN,
                    description=f"User {request.user.get_full_name() or request.user.username} logged in",
                    metadata={
                        'login_method': 'web' if 'text/html' in request.META.get('HTTP_ACCEPT', '') else 'api',
                        'user_agent': request.META.get('HTTP_USER_AGENT', ''),
                        'ip_address': self._get_client_ip(request)
                    },
                    request=request
                )
            
            # Log API access for sensitive endpoints
            if (request.path.startswith('/api/') and 
                response.status_code == 200 and
                hasattr(request, 'user') and 
                request.user.is_authenticated):
                
                # Log access to sensitive endpoints
                sensitive_endpoints = [
                    '/api/scheduling/audit-logs/',
                    '/api/scheduling/templates/',
                    '/api/scheduling/schedules/',
                    '/api/accounts/users/',
                    '/api/accounts/restaurants/'
                ]
                
                if any(request.path.startswith(endpoint) for endpoint in sensitive_endpoints):
                    AuditTrailService.log_activity(
                        user=request.user,
                        action=AuditActionType.VIEW,
                        description=f"Accessed {request.path}",
                        severity=AuditSeverity.LOW,
                        metadata={
                            'endpoint': request.path,
                            'method': request.method,
                            'response_status': response.status_code,
                            'query_params': dict(request.GET) if request.GET else None
                        },
                        request=request
                    )
        
        except Exception as e:
            # Don't let audit logging break the response
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error in audit middleware: {e}")
        
        finally:
            # Always clear audit context
            clear_audit_context()
        
        return response
    
    def process_exception(self, request, exception):
        """Clean up audit context on exception"""
        try:
            # Log critical errors for authenticated users
            if (hasattr(request, 'user') and 
                request.user.is_authenticated and
                not isinstance(exception, (KeyboardInterrupt, SystemExit))):
                
                AuditTrailService.log_activity(
                    user=request.user,
                    action=AuditActionType.VIEW,  # Using VIEW as a generic action
                    description=f"Error occurred: {str(exception)}",
                    severity=AuditSeverity.CRITICAL,
                    metadata={
                        'exception_type': type(exception).__name__,
                        'exception_message': str(exception),
                        'endpoint': request.path,
                        'method': request.method,
                        'error_occurred': True
                    },
                    request=request
                )
        
        except Exception as audit_error:
            # Don't let audit logging break error handling
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error in audit middleware exception handler: {audit_error}")
        
        finally:
            clear_audit_context()
        
        return None
    
    def _get_client_ip(self, request):
        """Get client IP address from request"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip


class RequestLoggingMiddleware(MiddlewareMixin):
    """
    Additional middleware for detailed request logging
    Logs all API requests for audit purposes
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        super().__init__(get_response)
    
    def process_request(self, request):
        """Log incoming requests"""
        # Only log API requests to avoid spam
        if not request.path.startswith('/api/'):
            return None
        
        # Skip logging for certain endpoints to avoid spam
        skip_endpoints = [
            '/api/health/',
            '/api/status/',
            '/api/ping/',
            '/api/auth/refresh/',
        ]
        
        if any(request.path.startswith(endpoint) for endpoint in skip_endpoints):
            return None
        
        # Store request start time for performance logging
        import time
        request._audit_start_time = time.time()
        
        return None
    
    def process_response(self, request, response):
        """Log request completion with performance metrics"""
        if not request.path.startswith('/api/'):
            return response
        
        # Skip logging for certain endpoints
        skip_endpoints = [
            '/api/health/',
            '/api/status/',
            '/api/ping/',
            '/api/auth/refresh/',
        ]
        
        if any(request.path.startswith(endpoint) for endpoint in skip_endpoints):
            return response
        
        try:
            # Calculate request duration
            duration = None
            if hasattr(request, '_audit_start_time'):
                import time
                duration = time.time() - request._audit_start_time
            
            # Log slow requests or errors
            should_log = (
                response.status_code >= 400 or  # Log all errors
                (duration and duration > 2.0) or  # Log slow requests (>2 seconds)
                request.method in ['POST', 'PUT', 'PATCH', 'DELETE']  # Log all mutations
            )
            
            if should_log and hasattr(request, 'user') and request.user.is_authenticated:
                severity = AuditSeverity.LOW
                if response.status_code >= 500:
                    severity = AuditSeverity.CRITICAL
                elif response.status_code >= 400:
                    severity = AuditSeverity.HIGH
                elif duration and duration > 5.0:
                    severity = AuditSeverity.MEDIUM
                
                description = f"{request.method} {request.path}"
                if response.status_code >= 400:
                    description += f" - Error {response.status_code}"
                elif duration and duration > 2.0:
                    description += f" - Slow request ({duration:.2f}s)"
                
                AuditTrailService.log_activity(
                    user=request.user,
                    action=AuditActionType.VIEW,
                    description=description,
                    severity=severity,
                    metadata={
                        'endpoint': request.path,
                        'method': request.method,
                        'status_code': response.status_code,
                        'duration_seconds': round(duration, 3) if duration else None,
                        'content_length': response.get('Content-Length'),
                        'query_params': dict(request.GET) if request.GET else None,
                        'is_slow_request': duration > 2.0 if duration else False,
                        'is_error': response.status_code >= 400
                    },
                    request=request
                )
        
        except Exception as e:
            # Don't let audit logging break the response
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error in request logging middleware: {e}")
        
        return response


class SecurityAuditMiddleware(MiddlewareMixin):
    """
    Middleware for security-related audit logging
    Tracks suspicious activities and security events
    """
    
    def process_request(self, request):
        """Check for suspicious request patterns"""
        try:
            # Track failed authentication attempts
            if (request.path.endswith('/login/') or 
                request.path.endswith('/api/auth/login/') or
                request.path.endswith('/api/token/')) and request.method == 'POST':
                request._is_auth_attempt = True
            
            # Track admin access attempts
            if request.path.startswith('/admin/'):
                request._is_admin_access = True
            
            # Track potential security threats
            suspicious_patterns = [
                'script',
                'javascript:',
                '<script',
                'eval(',
                'document.cookie',
                'union select',
                'drop table',
                '../',
                '..\\',
            ]
            
            # Check query parameters and path for suspicious content
            full_request = f"{request.path}?{request.META.get('QUERY_STRING', '')}"
            if any(pattern.lower() in full_request.lower() for pattern in suspicious_patterns):
                request._is_suspicious = True
        
        except Exception as e:
            # Don't let security audit break the request
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error in security audit middleware: {e}")
        
        return None
    
    def process_response(self, request, response):
        """Log security-related events"""
        try:
            # Log failed authentication attempts
            if (hasattr(request, '_is_auth_attempt') and 
                request._is_auth_attempt and 
                response.status_code in [400, 401, 403]):
                
                AuditTrailService.log_activity(
                    user=None,  # No user for failed login
                    action=AuditActionType.LOGIN,
                    description="Failed login attempt",
                    severity=AuditSeverity.HIGH,
                    metadata={
                        'status_code': response.status_code,
                        'ip_address': self._get_client_ip(request),
                        'user_agent': request.META.get('HTTP_USER_AGENT', ''),
                        'endpoint': request.path,
                        'failed_login': True
                    },
                    request=request
                )
            
            # Log admin access
            if (hasattr(request, '_is_admin_access') and 
                request._is_admin_access and
                hasattr(request, 'user') and 
                request.user.is_authenticated):
                
                AuditTrailService.log_activity(
                    user=request.user,
                    action=AuditActionType.VIEW,
                    description=f"Admin access: {request.path}",
                    severity=AuditSeverity.HIGH,
                    metadata={
                        'admin_access': True,
                        'endpoint': request.path,
                        'method': request.method,
                        'status_code': response.status_code,
                        'is_superuser': request.user.is_superuser,
                        'is_staff': request.user.is_staff
                    },
                    request=request
                )
            
            # Log suspicious requests
            if hasattr(request, '_is_suspicious') and request._is_suspicious:
                AuditTrailService.log_activity(
                    user=request.user if hasattr(request, 'user') and request.user.is_authenticated else None,
                    action=AuditActionType.VIEW,
                    description="Suspicious request detected",
                    severity=AuditSeverity.CRITICAL,
                    metadata={
                        'suspicious_request': True,
                        'endpoint': request.path,
                        'method': request.method,
                        'query_string': request.META.get('QUERY_STRING', ''),
                        'ip_address': self._get_client_ip(request),
                        'user_agent': request.META.get('HTTP_USER_AGENT', ''),
                        'status_code': response.status_code
                    },
                    request=request
                )
        
        except Exception as e:
            # Don't let security audit break the response
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error in security audit middleware: {e}")
        
        return response
    
    def _get_client_ip(self, request):
        """Get client IP address from request"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip