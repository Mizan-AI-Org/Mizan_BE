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
    tier = serializers.SerializerMethodField()
    is_paid = serializers.SerializerMethodField()

    class Meta:
        model = Subscription
        fields = [
            'id', 'status', 'tier', 'is_paid', 'billing_interval',
            'current_period_start', 'current_period_end',
            'cancel_at_period_end', 'trial_ends_at', 'plan',
        ]

    def get_tier(self, obj) -> str:
        return obj.effective_tier

    def get_is_paid(self, obj) -> bool:
        return obj.is_paid
