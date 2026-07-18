import stripe
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import Subscription, SubscriptionPlan
from .providers import resolve_payment_provider
from accounts.models import Restaurant


def get_starter_plan() -> SubscriptionPlan | None:
    return (
        SubscriptionPlan.objects.filter(tier=SubscriptionPlan.Tier.STARTER, is_active=True)
        .order_by("sort_order", "id")
        .first()
        or SubscriptionPlan.objects.filter(slug="starter", is_active=True).first()
    )


def ensure_starter_subscription(restaurant: Restaurant) -> Subscription:
    """Assign Starter + trial when a tenant has no plan yet (signup default)."""
    plan = get_starter_plan()
    sub, created = Subscription.objects.get_or_create(restaurant=restaurant)
    dirty_fields: list[str] = []

    if plan and sub.plan_id is None:
        sub.plan = plan
        dirty_fields.append("plan")

    if created or sub.status in {"incomplete", "incomplete_expired"}:
        sub.status = "trialing"
        dirty_fields.append("status")
        trial_days = (plan.trial_days if plan else 14) or 14
        if not sub.trial_ends_at:
            sub.trial_ends_at = timezone.now() + timedelta(days=trial_days)
            dirty_fields.append("trial_ends_at")

    if dirty_fields:
        sub.save(update_fields=[*set(dirty_fields), "updated_at"])
    return sub


def queue_upgrade_intent(
    restaurant: Restaurant,
    plan: SubscriptionPlan,
    billing_interval: str,
) -> Subscription:
    """Remember the plan the tenant wants until a payment wall can charge them."""
    sub = ensure_starter_subscription(restaurant)
    sub.pending_plan = plan
    sub.pending_billing_interval = billing_interval if billing_interval in {"month", "year"} else "month"
    sub.save(update_fields=["pending_plan", "pending_billing_interval", "updated_at"])
    return sub


def clear_upgrade_intent(subscription: Subscription) -> None:
    if not subscription.pending_plan_id and not subscription.pending_billing_interval:
        return
    subscription.pending_plan = None
    subscription.pending_billing_interval = ""
    subscription.save(update_fields=["pending_plan", "pending_billing_interval", "updated_at"])


class StripeService:
    def __init__(self):
        stripe.api_key = settings.STRIPE_SECRET_KEY

    def create_customer(self, restaurant: Restaurant):
        """Create a Stripe customer for the restaurant if one doesn't exist."""
        subscription = ensure_starter_subscription(restaurant)

        if not subscription.stripe_customer_id:
            try:
                owner = (
                    restaurant.staff.filter(role__in=["SUPER_ADMIN", "OWNER", "ADMIN"])
                    .order_by("created_at")
                    .first()
                )
                customer = stripe.Customer.create(
                    email=(owner.email if owner else restaurant.email) or None,
                    name=restaurant.name,
                    metadata={
                        "restaurant_id": str(restaurant.id),
                        "restaurant_name": restaurant.name,
                    },
                )
                subscription.stripe_customer_id = customer.id
                subscription.save(update_fields=["stripe_customer_id", "updated_at"])
                return customer
            except Exception as e:
                raise e
        return stripe.Customer.retrieve(subscription.stripe_customer_id)

    def create_checkout_session(self, restaurant, price_id, success_url, cancel_url):
        """Create a Stripe Checkout Session for a new subscription."""
        subscription = restaurant.subscription
        if not subscription.stripe_customer_id:
            self.create_customer(restaurant)
            subscription.refresh_from_db()

        checkout_session = stripe.checkout.Session.create(
            customer=subscription.stripe_customer_id,
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            success_url=success_url,
            cancel_url=cancel_url,
            client_reference_id=str(restaurant.id),
            allow_promotion_codes=True,
            metadata={
                "restaurant_id": str(restaurant.id),
                "price_id": price_id,
            },
            subscription_data={
                "metadata": {
                    "restaurant_id": str(restaurant.id),
                    "price_id": price_id,
                },
            },
        )
        return checkout_session.url

    def change_subscription_price(self, restaurant, price_id) -> dict:
        """Swap the price on an existing Stripe subscription (upgrade / downgrade)."""
        subscription = restaurant.subscription
        if not subscription.stripe_subscription_id:
            raise ValueError("No active provider subscription to change")

        stripe_sub = stripe.Subscription.retrieve(subscription.stripe_subscription_id)
        items = (stripe_sub.get("items") or {}).get("data") or []
        if not items:
            raise ValueError("Stripe subscription has no items to update")

        item_id = items[0]["id"]
        updated = stripe.Subscription.modify(
            subscription.stripe_subscription_id,
            items=[{"id": item_id, "price": price_id}],
            proration_behavior="create_prorations",
            metadata={
                "restaurant_id": str(restaurant.id),
                "price_id": price_id,
            },
        )
        return updated

    def create_portal_session(self, restaurant, return_url):
        """Create a generic Customer Portal session."""
        subscription = restaurant.subscription
        if not subscription.stripe_customer_id:
            self.create_customer(restaurant)
            subscription.refresh_from_db()

        portal_session = stripe.billing_portal.Session.create(
            customer=subscription.stripe_customer_id,
            return_url=return_url,
        )
        return portal_session.url

    def get_active_subscription(self, subscription_id):
        """Retrieve subscription details from Stripe."""
        try:
            return stripe.Subscription.retrieve(subscription_id)
        except Exception:
            return None


def start_plan_upgrade(
    restaurant: Restaurant,
    *,
    price_id: str,
    plan: SubscriptionPlan,
    billing_interval: str,
    success_url: str,
    cancel_url: str,
) -> dict:
    """Start an upgrade using the tenant's payment provider (or queue intent).

    Returns a dict the API serializes to the frontend:
      - action=redirect + url → send user to hosted checkout / payment wall
      - action=updated → in-place provider plan change completed
      - action=queued → payment wall not ready; intent stored on subscription
    """
    choice = resolve_payment_provider(restaurant)
    queue_upgrade_intent(restaurant, plan, billing_interval)

    can_charge = (
        choice.provider == "stripe"
        and choice.configured
        and bool((price_id or "").strip())
    )

    if can_charge:
        service = StripeService()
        sub = restaurant.subscription
        # Already on a Stripe subscription → change price instead of a second Checkout.
        if sub.stripe_subscription_id:
            updated = service.change_subscription_price(restaurant, price_id)
            _apply_local_stripe_update(sub, updated, plan, billing_interval)
            clear_upgrade_intent(sub)
            return {
                "action": "updated",
                "url": None,
                "provider": "stripe",
                "message": "Plan updated. Proration may appear on your next invoice.",
                "subscription_id": sub.id,
            }

        url = service.create_checkout_session(restaurant, price_id, success_url, cancel_url)
        return {
            "action": "redirect",
            "url": url,
            "provider": "stripe",
            "message": "Redirecting to checkout",
        }

    # Payment wall not ready, or Stripe ready but this plan has no price ID yet.
    reason = choice.reason
    if choice.provider == "stripe" and choice.configured and not (price_id or "").strip():
        reason = "Stripe is configured but this plan has no Stripe price ID yet"

    return {
        "action": "queued",
        "url": None,
        "provider": choice.provider,
        "message": (
            "Your upgrade request was saved. "
            "We'll complete billing when the payment wall for your country is available. "
            f"({reason})"
        ),
        "subscription_id": restaurant.subscription.id,
    }


def _apply_local_stripe_update(
    sub: Subscription,
    stripe_sub,
    plan: SubscriptionPlan,
    billing_interval: str,
) -> None:
    sub.plan = plan
    sub.billing_interval = billing_interval if billing_interval in {"month", "year"} else sub.billing_interval
    sub.status = stripe_sub.get("status") or sub.status
    sub.stripe_subscription_id = stripe_sub.get("id") or sub.stripe_subscription_id
    for attr, key in (
        ("current_period_start", "current_period_start"),
        ("current_period_end", "current_period_end"),
    ):
        value = stripe_sub.get(key)
        if value:
            setattr(sub, attr, timezone.datetime.fromtimestamp(value, tz=timezone.utc))

    # Paying ends the Starter signup trial — Growth/Enterprise never get a trial.
    stripe_status = (stripe_sub.get("status") or sub.status or "").strip()
    stripe_trial_end = stripe_sub.get("trial_end")
    if stripe_status == "active" or not stripe_trial_end:
        sub.trial_ends_at = None
    elif stripe_trial_end:
        sub.trial_ends_at = timezone.datetime.fromtimestamp(stripe_trial_end, tz=timezone.utc)
    sub.save()
