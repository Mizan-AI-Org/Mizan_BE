"""Cache key helpers for dashboard summary (invalidate on attendance changes)."""
from __future__ import annotations

from datetime import date

from django.core.cache import cache
from django.utils import timezone


def dashboard_summary_cache_key(restaurant_id, day: date | None = None) -> str:
    d = day or timezone.now().date()
    return f"dashboard:summary:v2:{restaurant_id}:{d.isoformat()}"


def invalidate_dashboard_summary(restaurant_id, day: date | None = None) -> None:
    try:
        cache.delete(dashboard_summary_cache_key(restaurant_id, day))
    except Exception:
        pass
