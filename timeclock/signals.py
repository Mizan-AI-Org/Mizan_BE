"""Bust dashboard summary cache when clock events change (fresher UI, shorter stale reads)."""
from django.db.models.signals import post_save
from django.dispatch import receiver

from core.dashboard_cache_keys import invalidate_dashboard_summary

from .models import ClockEvent


@receiver(post_save, sender=ClockEvent)
def bust_dashboard_summary_on_clock_event(sender, instance, **kwargs):
    staff = getattr(instance, "staff", None)
    rid = getattr(staff, "restaurant_id", None) if staff else None
    if rid:
        invalidate_dashboard_summary(rid)
