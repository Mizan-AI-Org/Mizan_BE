"""
Notify incident assignees via WhatsApp when assignment is set or changes.
"""
from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

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
