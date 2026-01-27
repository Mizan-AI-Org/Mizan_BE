"""
Utility functions for Mizan AI
"""
from django.utils import timezone
from datetime import timedelta
import re


def get_date_range(days=7):
    """Get date range for last N days"""
    end_date = timezone.now().date()
    start_date = end_date - timedelta(days=days)
    return start_date, end_date


def format_currency(amount, currency='USD'):
    """Format amount as currency"""
    if currency == 'USD':
        return f"${amount:.2f}"
    elif currency == 'EUR':
        return f"€{amount:.2f}"
    else:
        return f"{amount:.2f} {currency}"


def calculate_percentage(part, total):
    """Calculate percentage"""
    if total == 0:
        return 0
    return round((part / total) * 100, 2)


def paginate_queryset(queryset, page=1, page_size=20):
    """Paginate queryset"""
    start = (page - 1) * page_size
    end = start + page_size
    return queryset[start:end]


def get_tenant_from_request(request):
    """
    Extract tenant from request.
    Priority:
    1. From X-Restaurant-ID header
    2. From JWT token (restaurant_id claim)
    3. From query parameter restaurant_id
    """
    from accounts.models import Restaurant
    from rest_framework_simplejwt.authentication import JWTAuthentication
    from rest_framework_simplejwt.exceptions import InvalidToken
    
    # Check header
    restaurant_id = request.META.get('HTTP_X_RESTAURANT_ID')
    if restaurant_id:
        try:
            return Restaurant.objects.get(id=restaurant_id)
        except Restaurant.DoesNotExist:
            return None
    
    # Check query parameter
    restaurant_id = request.GET.get('restaurant_id')
    if restaurant_id:
        try:
            return Restaurant.objects.get(id=restaurant_id)
        except Restaurant.DoesNotExist:
            return None
    
    # Check JWT token
    if request.user and request.user.is_authenticated:
        try:
            # User might have a primary restaurant
            user_roles = request.user.restaurant_roles.filter(is_primary=True)
            if user_roles.exists():
                return user_roles.first().restaurant
        except:
            pass
    
    return None

def build_tenant_context(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return None
    restaurant = getattr(request, 'tenant', None) or getattr(user, 'restaurant', None)
    if not restaurant:
        return None
    full_name = f"{user.first_name} {user.last_name}".strip()
    email = user.email
    params = {}
    params['timezone'] = getattr(restaurant, 'timezone', None)
    params['language'] = getattr(restaurant, 'language', None)
    params['currency'] = getattr(restaurant, 'currency', None)
    params['operating_hours'] = getattr(restaurant, 'operating_hours', None)
    return {
        'user_name': full_name,
        'user_email': email,
        'restaurant_id': str(restaurant.id),
        'restaurant_name': restaurant.name,
        'params': params,
    }


def resolve_agent_restaurant_and_user(request=None, payload=None):
    """
    Resolve (restaurant, user) context for agent-authenticated endpoints.

    These endpoints are typically called by a trusted AI agent (Lua) and may not include
    restaurant_id explicitly. We try to infer it from:
    - explicit restaurant_id / restaurantId (payload/query)
    - sessionId in the form "tenant-<restaurant_uuid>-user-<user_uuid>"
    - userId (payload)
    - email / emailAddress (payload)
    - phone / mobileNumber / reporter_phone (payload)
    - JWT access token (payload.token or payload.metadata.token)
    - request headers / query params (when request provided)

    Returns: (restaurant_or_None, user_or_None)
    """
    from accounts.models import Restaurant, CustomUser

    data = payload or {}
    # For GET endpoints, allow query params / headers as inputs
    if request is not None:
        try:
            # Merge query params without overwriting explicit payload keys
            qp = getattr(request, 'query_params', None) or getattr(request, 'GET', None) or {}
            for k, v in getattr(qp, 'items', lambda: [])():
                data.setdefault(k, v)
        except Exception:
            pass

        try:
            hdr_rest_id = request.META.get('HTTP_X_RESTAURANT_ID')
            if hdr_rest_id and not data.get('restaurant_id'):
                data['restaurant_id'] = hdr_rest_id
        except Exception:
            pass

    meta = data.get('metadata') if isinstance(data.get('metadata'), dict) else {}

    def _get_first(*keys):
        for k in keys:
            v = data.get(k)
            if v:
                return v
        for k in keys:
            v = meta.get(k)
            if v:
                return v
        return None

    # 1) Direct restaurant id
    restaurant_id = _get_first('restaurant_id', 'restaurantId', 'restaurant')
    if restaurant_id:
        try:
            return Restaurant.objects.get(id=restaurant_id), None
        except Exception:
            pass

    # 2) SessionId pattern: tenant-<restaurant>-user-<user>
    session_id = _get_first('sessionId', 'session_id')
    if session_id:
        m = re.search(r"tenant-([0-9a-fA-F-]{8,})-user-([0-9a-fA-F-]{8,})", str(session_id))
        if m:
            rest_id = m.group(1)
            user_id = m.group(2)
            user_obj = None
            try:
                user_obj = CustomUser.objects.filter(id=user_id).select_related('restaurant').first()
            except Exception:
                user_obj = None
            if user_obj and getattr(user_obj, 'restaurant_id', None):
                return user_obj.restaurant, user_obj
            try:
                rest_obj = Restaurant.objects.get(id=rest_id)
                return rest_obj, user_obj
            except Exception:
                pass

    # 3) UserId
    user_id = _get_first('userId', 'user_id', 'staffId', 'staff_id')
    if user_id:
        try:
            user_obj = CustomUser.objects.filter(id=user_id).select_related('restaurant').first()
            if user_obj and user_obj.restaurant:
                return user_obj.restaurant, user_obj
        except Exception:
            pass

    # 4) Email
    email = _get_first('email', 'emailAddress')
    if email:
        try:
            user_obj = CustomUser.objects.filter(email__iexact=str(email).strip()).select_related('restaurant').first()
            if user_obj and user_obj.restaurant:
                return user_obj.restaurant, user_obj
        except Exception:
            pass

    # 5) Phone
    phone = _get_first('phone', 'mobileNumber', 'reporter_phone', 'reporterPhone')
    if phone:
        digits = ''.join(filter(str.isdigit, str(phone)))
        patterns = [digits, digits[-10:] if len(digits) > 10 else digits, f"+{digits}"]
        try:
            for p in patterns:
                user_obj = CustomUser.objects.filter(phone__icontains=p).select_related('restaurant').first()
                if user_obj and user_obj.restaurant:
                    return user_obj.restaurant, user_obj
        except Exception:
            pass

    # 6) JWT token -> user -> restaurant
    token = _get_first('token', 'accessToken', 'access_token')
    if token:
        try:
            from rest_framework_simplejwt.authentication import JWTAuthentication
            jwt_auth = JWTAuthentication()
            validated = jwt_auth.get_validated_token(str(token))
            user_obj = jwt_auth.get_user(validated)
            if user_obj and getattr(user_obj, 'restaurant', None):
                return user_obj.restaurant, user_obj
        except Exception:
            pass

    return None, None
