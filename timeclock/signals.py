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
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone

from core.dashboard_cache_keys import invalidate_dashboard_summary

from .models import ClockEvent
from .services import restaurant_ids_for_clock_event


@receiver(post_save, sender=ClockEvent)
def bust_dashboard_summary_on_clock_event(sender, instance, **kwargs):
    # Resolve once: both the dashboard summary feed and Miya's
    # attendance-report feed are keyed by (restaurant_id, date). A single
    # clock event legitimately belongs to several restaurants (primary
    # restaurant, the BusinessLocation's owner, any active
    # StaffRestaurantLink) so we fan out across them all — otherwise the
    # feed at the venue where the event actually happened stays stale
    # until the TTL expires.
    try:
        event_date = instance.timestamp.date()
    except Exception:
        event_date = timezone.now().date()
    event_date_iso = event_date.isoformat()

    for rid in restaurant_ids_for_clock_event(instance):
        try:
            invalidate_dashboard_summary(rid)
        except Exception:
            # Cache invalidation must never break a clock-in commit.
            pass
        try:
            # Lazy import keeps the signals module importable even when
            # the timeclock views haven't been loaded yet (e.g. during
            # short Django shell sessions or migration rollbacks).
            from timeclock.views import invalidate_attendance_report

            invalidate_attendance_report(rid, event_date_iso)
        except Exception:
            pass


def _bust_attendance_for_shift(instance) -> None:
    """Invalidate the attendance-report cache for the shift's tenant+date.

    Called on AssignedShift writes and deletes so a shift added or
    reassigned through the dashboard is reflected in Miya's next
    ``agent_attendance_report`` call without waiting out the 30s TTL.
    """
    try:
        from timeclock.views import invalidate_attendance_report

        restaurant_id = getattr(getattr(instance, "schedule", None), "restaurant_id", None)
        shift_date = getattr(instance, "shift_date", None)
        if restaurant_id is None:
            return
        iso = shift_date.isoformat() if shift_date else None
        invalidate_attendance_report(restaurant_id, iso)
    except Exception:
        # Cache invalidation is an optimisation — never propagate.
        pass


# Intentionally defined here (and not in scheduling/signals.py) so the
# attendance-report cache lives wherever its feed is computed. Using a
# string sender reference via apps.get_model keeps the circular-import
# risk zero even though AssignedShift belongs to the scheduling app.
def _wire_assigned_shift_hooks():
    from django.apps import apps as django_apps

    try:
        AssignedShift = django_apps.get_model("scheduling", "AssignedShift")
    except Exception:
        return

    post_save.connect(
        lambda sender, instance, **_: _bust_attendance_for_shift(instance),
        sender=AssignedShift,
        weak=False,
        dispatch_uid="timeclock.attendance_bust_on_shift_save",
    )
    post_delete.connect(
        lambda sender, instance, **_: _bust_attendance_for_shift(instance),
        sender=AssignedShift,
        weak=False,
        dispatch_uid="timeclock.attendance_bust_on_shift_delete",
    )


_wire_assigned_shift_hooks()
