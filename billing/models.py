from django.db import models
from django.utils.translation import gettext_lazy as _


class SubscriptionPlan(models.Model):
    """A published pricing tier.

    We store both monthly and yearly Stripe price IDs so the frontend can
    offer a toggle without needing two DB rows per tier. Limits are stored
    here too (``max_locations`` / ``max_staff``) so the entitlements endpoint
    can return them without reading config.
    """

    INTERVAL_CHOICES = (
        ('month', 'Monthly'),
        ('year', 'Yearly'),
    )

    class Tier(models.TextChoices):
        FREE = 'FREE', _('Free')
        STARTER = 'STARTER', _('Starter')
        GROWTH = 'GROWTH', _('Growth')
        ENTERPRISE = 'ENTERPRISE', _('Enterprise')

    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    tier = models.CharField(
        max_length=20, choices=Tier.choices, default=Tier.STARTER, db_index=True,
    )
    description = models.TextField(blank=True, default="")

    # Legacy single price fields — kept populated (=monthly) for backward
    # compatibility with existing code & the DB constraint. New code reads
    # ``price_monthly`` / ``price_yearly``.
    stripe_price_id = models.CharField(max_length=100, blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)

    # New, tier-level pricing.
    price_monthly = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    price_yearly = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    stripe_price_id_monthly = models.CharField(max_length=120, blank=True, default="")
    stripe_price_id_yearly = models.CharField(max_length=120, blank=True, default="")

    currency = models.CharField(max_length=3, default='USD')
    interval = models.CharField(max_length=10, choices=INTERVAL_CHOICES, default='month')
    features = models.JSONField(default=list, help_text="List of features included in this plan")
    # Stable keys the frontend can feature-gate on (e.g. ["pos", "eatnow", "auto_po"]).
    feature_keys = models.JSONField(default=list, blank=True)

    # Usage limits. ``None`` means unlimited.
    max_locations = models.PositiveIntegerField(null=True, blank=True)
    max_staff = models.PositiveIntegerField(null=True, blank=True)

    # Presentation hints.
    badge = models.CharField(
        max_length=40, blank=True, default="",
        help_text="e.g. 'Most popular' — shown above the card on the pricing page.",
    )
    highlight = models.BooleanField(
        default=False,
        help_text="Highlight this tier on the pricing page (ring, shadow).",
    )
    cta_label = models.CharField(max_length=40, blank=True, default="")
    sort_order = models.PositiveSmallIntegerField(default=0, db_index=True)
    contact_sales = models.BooleanField(
        default=False,
        help_text="If true, the CTA opens a 'Contact sales' flow instead of Stripe checkout.",
    )
    trial_days = models.PositiveSmallIntegerField(default=0)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'price_monthly']

    def __str__(self):
        return f"{self.name} ({self.tier})"


class Subscription(models.Model):
    STATUS_CHOICES = (
        ('incomplete', 'Incomplete'),
        ('incomplete_expired', 'Incomplete Expired'),
        ('trialing', 'Trialing'),
        ('active', 'Active'),
        ('past_due', 'Past Due'),
        ('canceled', 'Canceled'),
        ('unpaid', 'Unpaid'),
    )

    restaurant = models.OneToOneField(
        'accounts.Restaurant',
        on_delete=models.CASCADE,
        related_name='subscription'
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    stripe_customer_id = models.CharField(max_length=100, blank=True, null=True)
    stripe_subscription_id = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='incomplete')
    # Interval of the active Stripe price ("month" / "year"). Populated from
    # webhook so the UI knows which toggle position to render.
    billing_interval = models.CharField(max_length=10, blank=True, default="")

    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    trial_ends_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.restaurant.name} - {self.status}"

    # ----- entitlement helpers ------------------------------------------------
    @property
    def is_paid(self) -> bool:
        return self.status in {"active", "trialing"}

    @property
    def effective_tier(self) -> str:
        """Return the currently entitled tier.

        During the pilot we grant ``GROWTH`` to any tenant without an active
        paid subscription so nothing breaks while pricing rolls out. Flip
        ``settings.BILLING_PILOT_DEFAULT_TIER`` to ``FREE`` / ``STARTER`` to
        tighten the default later.
        """
        from django.conf import settings

        if self.is_paid and self.plan:
            return self.plan.tier
        return getattr(settings, "BILLING_PILOT_DEFAULT_TIER", SubscriptionPlan.Tier.GROWTH)
