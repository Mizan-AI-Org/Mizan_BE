"""Signals for the finance app.

Invalidates the cached ``agent_list_invoices`` slices whenever an
invoice is created, paid, voided, or otherwise mutated — whether the
change originates from a Miya tool call, the manager dashboard, a POS
webhook, a scheduled task, or the Django admin. Without this hook
Miya's read cache can serve up to ``_INVOICES_CACHE_TTL`` seconds of
stale data after a ``record_invoice`` / ``mark_invoice_paid``, which
confuses manager-facing confirmations.
"""
from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from finance.models import Invoice


@receiver(post_save, sender=Invoice)
def bust_agent_invoices_cache_on_save(sender, instance, **kwargs):
    # Lazy import so circular-import risk stays zero regardless of the
    # app load order (views_agent imports the Invoice model; the signals
    # module is wired via finance/apps.py.ready()).
    try:
        from finance.views_agent import invalidate_invoices_cache
    except Exception:
        return

    rid = getattr(instance, "restaurant_id", None)
    if rid:
        try:
            invalidate_invoices_cache(rid)
        except Exception:
            # Cache invalidation is a best-effort optimisation — never
            # let it raise from a model save and turn a successful
            # write into a 500.
            pass
