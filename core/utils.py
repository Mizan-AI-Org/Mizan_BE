"""
Utility functions for Mizan AI
"""
from django.utils import timezone
from datetime import timedelta


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