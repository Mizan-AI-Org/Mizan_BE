"""Plan seat / location limits for tenant entitlements."""
from __future__ import annotations

from django.utils import timezone

from .models import SubscriptionPlan
from .services import ensure_starter_subscription


def resolve_plan_for_restaurant(restaurant) -> SubscriptionPlan | None:
    subscription = ensure_starter_subscription(restaurant)
    if subscription.plan_id:
        return subscription.plan
    return (
        SubscriptionPlan.objects.filter(
            tier=subscription.effective_tier, is_active=True
        )
        .order_by("sort_order", "id")
        .first()
    )


def count_staff_seats(restaurant) -> int:
    """Seats in use: active users + pending email invites + pending WhatsApp activations."""
    from accounts.models import CustomUser, StaffActivationRecord, UserInvitation

    active_users = CustomUser.objects.filter(
        restaurant=restaurant,
        is_active=True,
    ).count()

    pending_invites = UserInvitation.objects.filter(
        restaurant=restaurant,
        is_accepted=False,
        expires_at__gt=timezone.now(),
    ).count()

    pending_activations = StaffActivationRecord.objects.filter(
        restaurant=restaurant,
        status=StaffActivationRecord.STATUS_NOT_ACTIVATED,
    ).count()

    return active_users + pending_invites + pending_activations


def staff_limit_for_restaurant(restaurant) -> int | None:
    """Return max staff seats, or None when unlimited."""
    plan = resolve_plan_for_restaurant(restaurant)
    if not plan:
        return None
    return plan.max_staff


def assert_can_add_staff(restaurant, additional: int = 1) -> tuple[bool, str | None]:
    """Return (ok, error_message). ``additional`` is how many new seats are requested."""
    if additional <= 0:
        return True, None

    max_staff = staff_limit_for_restaurant(restaurant)
    if max_staff is None:
        return True, None

    used = count_staff_seats(restaurant)
    remaining = max_staff - used
    if remaining >= additional:
        return True, None

    plan = resolve_plan_for_restaurant(restaurant)
    plan_name = plan.name if plan else "current"
    if remaining <= 0:
        return (
            False,
            (
                f"Your {plan_name} plan allows up to {max_staff} staff "
                f"({used} seats in use). Upgrade your plan to add more."
            ),
        )
    return (
        False,
        (
            f"Your {plan_name} plan allows up to {max_staff} staff "
            f"({used} in use, {remaining} seat{'s' if remaining != 1 else ''} left). "
            f"Reduce this invite or upgrade your plan."
        ),
    )
