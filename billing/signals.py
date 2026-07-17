from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender="accounts.Restaurant")
def assign_starter_subscription_on_restaurant_create(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        from billing.services import ensure_starter_subscription

        ensure_starter_subscription(instance)
    except Exception:
        import logging

        logging.getLogger(__name__).exception(
            "Failed to assign Starter subscription for restaurant %s", instance.id
        )
