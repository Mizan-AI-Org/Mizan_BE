"""
Signals for the staff app:

- Notify incident assignees via WhatsApp when assignment is set or changes.
- Invalidate agent/dashboard caches when incidents or requests change, so
  the Miya feeds and dashboard widgets stay fresh across processes (Redis-
  backed cache; otherwise local-memory fallback per-process).
"""
from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from staff.models import StaffRequest
from staff.models_task import SafetyConcernReport


@receiver(pre_save, sender=SafetyConcernReport)
def safety_report_cache_prev_assigned(sender, instance, **kwargs):
    if not instance.pk:
        instance._prev_assigned_to_id = None
        return
    try:
        instance._prev_assigned_to_id = (
            SafetyConcernReport.objects.only("assigned_to_id")
            .get(pk=instance.pk)
            .assigned_to_id
        )
    except SafetyConcernReport.DoesNotExist:
        instance._prev_assigned_to_id = None


@receiver(post_save, sender=SafetyConcernReport)
def safety_report_notify_assignee_whatsapp(sender, instance, created, **kwargs):
    from staff.incident_assignee_notify import (
        schedule_notify_assignee_whatsapp_for_incident,
    )

    prev = getattr(instance, "_prev_assigned_to_id", None)
    cur = instance.assigned_to_id
    if not cur:
        return
    if prev == cur:
        return

    pk = instance.pk
    transaction.on_commit(
        lambda: schedule_notify_assignee_whatsapp_for_incident(pk)
    )


# ---------------------------------------------------------------------------
# Cache invalidation
#
# Miya lists incidents via agent_list_incidents (staff/views_agent.py) and
# requests via agent_list_staff_requests. Both are read-through-cached to
# shield RDS from every polling/Miya turn. Whenever a row changes we wipe
# the per-restaurant slices of those feeds so the next Miya question
# returns fresh data instead of up-to-30s stale rows. Best-effort: the
# helper swallows cache errors so a Redis hiccup can never break a write.
# ---------------------------------------------------------------------------

def _bust_agent_incidents_for(restaurant_id) -> None:
    try:
        from staff.views_agent import _invalidate_staff_incidents_cache

        _invalidate_staff_incidents_cache(restaurant_id)
    except Exception:
        # Never let cache invalidation turn a successful write into a 500.
        pass


def _bust_agent_requests_for(restaurant_id) -> None:
    try:
        from staff.views_agent import _invalidate_staff_requests_cache

        _invalidate_staff_requests_cache(restaurant_id)
    except Exception:
        pass


@receiver(post_save, sender=SafetyConcernReport)
def safety_report_bust_agent_cache(sender, instance, **kwargs):
    rid = getattr(instance, "restaurant_id", None)
    if rid:
        _bust_agent_incidents_for(rid)


@receiver(post_save, sender=StaffRequest)
def staff_request_bust_agent_cache(sender, instance, **kwargs):
    rid = getattr(instance, "restaurant_id", None)
    if rid:
        _bust_agent_requests_for(rid)
