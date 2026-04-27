"""Bust dashboard summary cache when clock events change (fresher UI, shorter stale reads).

Why we fan out to every restaurant
----------------------------------
A clock event can legitimately belong to more than one restaurant in
the dashboard's eyes:

* the restaurant that owns the BusinessLocation where it was recorded,
* the staff member's primary restaurant, and
* any active StaffRestaurantLink (multi-restaurant staff).

If we only bust the staff's primary restaurant — which is what the
old signal did — the dashboard for the venue where the event actually
happened keeps serving a stale "0 clock-ins" payload until the
``dashboard_summary_cache`` TTL expires. Managers see a frozen widget
even though the helper at read-time returns the right data on the
next forced refresh. Fanning out keeps reads and invalidations
symmetric.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver

from core.dashboard_cache_keys import invalidate_dashboard_summary

from .models import ClockEvent
from .services import restaurant_ids_for_clock_event


@receiver(post_save, sender=ClockEvent)
def bust_dashboard_summary_on_clock_event(sender, instance, **kwargs):
    for rid in restaurant_ids_for_clock_event(instance):
        try:
            invalidate_dashboard_summary(rid)
        except Exception:
            # Cache invalidation must never break a clock-in commit.
            continue
