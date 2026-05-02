"""
Read-through cache helpers: reduce RDS load when Redis is available.
If cache get/set fails (Redis down, serialization), callers still get fresh data.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, TypeVar

from django.core.cache import cache

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Roles that can appear as suffix in staff cache keys.
_STAFF_ROLE_KEYS = [
    "all", "CHEF", "WAITER", "MANAGER", "SERVER", "CASHIER",
    "BARTENDER", "HOST", "SUPERVISOR", "OWNER", "ADMIN", "SUPER_ADMIN",
    "KITCHEN", "RUNNER", "BUSSER", "BARISTA", "COOK",
]

# Memory types that can appear in memory-list cache keys.
_MEMORY_TYPE_KEYS = ["", "preference", "correction", "fact", "pattern"]


def safe_cache_get(key: str) -> Any | None:
    try:
        return cache.get(key)
    except Exception as exc:
        logger.warning("cache get failed key=%s: %s", key, exc)
        return None


def safe_cache_set(key: str, value: Any, timeout: int) -> None:
    try:
        cache.set(key, value, timeout)
    except Exception as exc:
        logger.warning("cache set failed key=%s: %s", key, exc)


def safe_cache_delete(key: str) -> None:
    """Best-effort cache invalidation. Never raises from the cache layer so
    that a Redis hiccup can't turn a successful DB write into a 500.
    """
    try:
        cache.delete(key)
    except Exception as exc:
        logger.warning("cache delete failed key=%s: %s", key, exc)


def invalidate_staff_caches(restaurant_id: str) -> None:
    """Bust staff-list and staff-count caches for a restaurant.

    Call after any roster change (create/activate/deactivate/role change) so
    Miya sees the updated staff on the very next tool call instead of waiting
    for the TTL to expire.
    """
    rid = str(restaurant_id)
    keys = [f"agent:sched:staff_count_breakdown:{rid}"]
    for role in _STAFF_ROLE_KEYS:
        keys.append(f"agent:sched:staff_list:{rid}:{role}")
        keys.append(f"agent:sched:staff_count_only:{rid}:{role}")
    try:
        cache.delete_many(keys)
    except Exception as exc:
        logger.warning("invalidate_staff_caches failed rid=%s: %s", rid, exc)


def invalidate_memory_caches(restaurant_id: str) -> None:
    """Bust agent-memory-list caches for a restaurant.

    Call after any AgentMemory create/delete so recall_memories returns
    fresh results immediately rather than serving a stale list until TTL expiry.
    """
    rid = str(restaurant_id)
    keys = [f"agent:sched:memory_list:{rid}:{mt}:" for mt in _MEMORY_TYPE_KEYS]
    try:
        cache.delete_many(keys)
    except Exception as exc:
        logger.warning("invalidate_memory_caches failed rid=%s: %s", rid, exc)


def get_or_set(key: str, timeout: int, factory: Callable[[], T]) -> T:
    """Return cached value or compute, store, and return. Never raises from cache layer."""
    hit = safe_cache_get(key)
    if hit is not None:
        return hit  # type: ignore[return-value]
    data = factory()
    safe_cache_set(key, data, timeout)
    return data
