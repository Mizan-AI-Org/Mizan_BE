"""Real-time dashboard task widget invalidation via WebSocket."""

from __future__ import annotations

import logging
from typing import Iterable

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)

_TASK_NOTIFICATION_TYPES = frozenset(
    {
        "TASK_ASSIGNED",
        "TASK_COMPLETED",
        "TASK_OVERDUE",
        "TASK_ESCALATION",
    }
)


def broadcast_tasks_invalidate(
    restaurant,
    *,
    reason: str = "task_updated",
    task_id: str | None = None,
    user_ids: Iterable | None = None,
) -> None:
    """Tell connected dashboard clients to refresh tasks-demands."""
    if restaurant is None:
        return
    try:
        from accounts.models import CustomUser

        channel_layer = get_channel_layer()
        if channel_layer is None:
            return

        if user_ids is None:
            qs = CustomUser.objects.filter(
                restaurant=restaurant,
                is_active=True,
                role__in=("SUPER_ADMIN", "OWNER", "ADMIN", "MANAGER"),
            ).values_list("id", flat=True)
            user_ids = list(qs)

        payload = {
            "type": "tasks_invalidate",
            "reason": reason,
            "task_id": str(task_id) if task_id else None,
            "restaurant_id": str(getattr(restaurant, "id", restaurant)),
        }
        for uid in user_ids:
            group = f"user_{uid}_notifications"
            async_to_sync(channel_layer.group_send)(group, payload)
    except Exception:
        logger.exception("broadcast_tasks_invalidate failed restaurant=%s", getattr(restaurant, "id", restaurant))


def maybe_broadcast_for_notification_type(notification_type: str, restaurant, **kwargs) -> None:
    if (notification_type or "").upper() in _TASK_NOTIFICATION_TYPES:
        broadcast_tasks_invalidate(restaurant, reason=notification_type or "task_updated", **kwargs)
