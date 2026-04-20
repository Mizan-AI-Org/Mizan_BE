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


def get_or_set(key: str, timeout: int, factory: Callable[[], T]) -> T:
    """Return cached value or compute, store, and return. Never raises from cache layer."""
    hit = safe_cache_get(key)
    if hit is not None:
        return hit  # type: ignore[return-value]
    data = factory()
    safe_cache_set(key, data, timeout)
    return data
