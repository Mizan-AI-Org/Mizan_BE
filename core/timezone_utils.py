"""
Timezone utilities for restaurant operations.
Default: Africa/Casablanca (Casablanca, Morocco).
"""
import zoneinfo
from datetime import datetime
from django.utils import timezone as dj_timezone

DEFAULT_TIMEZONE = "Africa/Casablanca"


def get_restaurant_timezone(restaurant):
    """Get timezone string for a restaurant. Default: Africa/Casablanca."""
    if restaurant and getattr(restaurant, "timezone", None):
        tz_str = str(restaurant.timezone).strip()
        if tz_str:
            return tz_str
    return DEFAULT_TIMEZONE


def to_restaurant_local(dt, restaurant):
    """
    Convert datetime (naive UTC or timezone-aware) to restaurant's local timezone.
    Returns naive datetime in restaurant's local time.
    """
    if dt is None:
        return None
    tz_str = get_restaurant_timezone(restaurant)
    try:
        tz = zoneinfo.ZoneInfo(tz_str)
    except zoneinfo.ZoneInfoNotFoundError:
        tz = zoneinfo.ZoneInfo(DEFAULT_TIMEZONE)
    if dj_timezone.is_naive(dt):
        dt = dj_timezone.make_aware(dt, dj_timezone.utc)
    return dt.astimezone(tz).replace(tzinfo=None)


def to_utc(dt, tz_str=None):
    """
    Convert naive datetime in given timezone to UTC.
    If tz_str is None, uses DEFAULT_TIMEZONE.
    """
    if dt is None:
        return None
    tz_str = tz_str or DEFAULT_TIMEZONE
    try:
        tz = zoneinfo.ZoneInfo(tz_str)
    except zoneinfo.ZoneInfoNotFoundError:
        tz = zoneinfo.ZoneInfo(DEFAULT_TIMEZONE)
    local = dt if isinstance(dt, datetime) else datetime.combine(dt.date(), dt.time())
    return dj_timezone.make_aware(local, tz).astimezone(dj_timezone.utc)


def now_restaurant_local(restaurant):
    """Current time in restaurant's local timezone (naive)."""
    return to_restaurant_local(dj_timezone.now(), restaurant)


def format_restaurant_time(dt, restaurant, fmt="%Y-%m-%d %H:%M"):
    """Format datetime in restaurant's local timezone."""
    local = to_restaurant_local(dt, restaurant)
    return local.strftime(fmt) if local else ""
