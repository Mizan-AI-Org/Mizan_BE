from rest_framework import serializers

from .models import SubscriptionPlan, Subscription


class SubscriptionPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionPlan
        fields = [
            'id', 'slug', 'tier', 'name', 'description',
            'price', 'price_monthly', 'price_yearly',
            'currency', 'interval',
            'stripe_price_id', 'stripe_price_id_monthly', 'stripe_price_id_yearly',
            'features', 'feature_keys',
            'max_locations', 'max_staff',
            'badge', 'highlight', 'cta_label', 'contact_sales',
            'sort_order', 'trial_days', 'is_active',
        ]


class SubscriptionSerializer(serializers.ModelSerializer):
    plan = SubscriptionPlanSerializer(read_only=True)
    pending_plan = SubscriptionPlanSerializer(read_only=True)
    tier = serializers.SerializerMethodField()
    is_paid = serializers.SerializerMethodField()
    has_provider_subscription = serializers.SerializerMethodField()
    payment_provider = serializers.SerializerMethodField()

    class Meta:
        model = Subscription
        fields = [
            'id', 'status', 'tier', 'is_paid', 'has_provider_subscription',
            'payment_provider', 'billing_interval',
            'current_period_start', 'current_period_end',
            'cancel_at_period_end', 'trial_ends_at', 'plan',
            'pending_plan', 'pending_billing_interval',
        ]

    def get_tier(self, obj) -> str:
        return obj.effective_tier

    def get_is_paid(self, obj) -> bool:
        return obj.is_paid

    def get_has_provider_subscription(self, obj) -> bool:
        return obj.has_provider_subscription

    def get_payment_provider(self, obj) -> dict:
        from .providers import resolve_payment_provider

        choice = resolve_payment_provider(obj.restaurant)
        return {
            "id": choice.provider,
            "configured": choice.configured,
            "reason": choice.reason,
        }
