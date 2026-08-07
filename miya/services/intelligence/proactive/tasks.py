"""Celery entrypoints — Daily Operations Intelligence sweeps."""
from __future__ import annotations

import logging

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger("miya.intelligence.proactive.tasks")

_MANAGER_ROLES = ("SUPER_ADMIN", "OWNER", "ADMIN", "MANAGER")


@shared_task(name="miya.services.intelligence.proactive.tasks.daily_ops_intelligence_sweep")
def daily_ops_intelligence_sweep(period: str = "morning") -> dict:
    """
    Morning (default) proactive briefing for managers.
    Uses prefs, quiet hours, severity, and fingerprint dedupe — no spam.
    """
    from accounts.models import CustomUser, Restaurant
    from miya.services.intelligence.proactive.delivery import build_and_maybe_deliver

    period = (period or "morning").strip().lower()
    if period not in {"morning", "evening"}:
        period = "morning"

    summary = {
        "period": period,
        "sent": 0,
        "skipped": 0,
        "failed": 0,
        "restaurants": 0,
        "at": timezone.now().isoformat(),
    }

    for restaurant in Restaurant.objects.all().iterator(chunk_size=40):
        summary["restaurants"] += 1
        managers = CustomUser.objects.filter(
            restaurant_id=restaurant.id,
            is_active=True,
        ).filter(
            Q(role__in=_MANAGER_ROLES)
            | Q(role__icontains="MANAGER")
            | Q(role__icontains="OWNER")
            | Q(role__icontains="ADMIN")
        )
        for manager in managers.iterator(chunk_size=50):
            try:
                outcome = build_and_maybe_deliver(
                    restaurant=restaurant,
                    manager=manager,
                    period=period,
                )
                if outcome.get("sent"):
                    summary["sent"] += 1
                elif outcome.get("reason") in ("send_failed", "provider_rejected"):
                    summary["failed"] += 1
                else:
                    summary["skipped"] += 1
            except Exception:
                summary["failed"] += 1
                logger.exception(
                    "daily_ops_intelligence_sweep failed restaurant=%s user=%s",
                    restaurant.id,
                    manager.pk,
                )

    if summary["sent"] or summary["failed"]:
        logger.info("daily_ops_intelligence_sweep: %s", summary)
    return summary


@shared_task(name="miya.services.intelligence.proactive.tasks.daily_ops_intelligence_morning")
def daily_ops_intelligence_morning() -> dict:
    return daily_ops_intelligence_sweep(period="morning")
