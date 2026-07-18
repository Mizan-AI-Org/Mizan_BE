from rest_framework import serializers
from django.contrib.auth import get_user_model
from accounts.models import Restaurant, BusinessLocation, AuditLog
from billing.models import Subscription, SubscriptionPlan
from .lifecycle import tenant_is_deactivated, tenant_is_suspended

User = get_user_model()


class PlatformTenantListSerializer(serializers.ModelSerializer):
    staff_count = serializers.IntegerField(read_only=True)
    subscription_status = serializers.CharField(read_only=True, allow_null=True)
    subscription_plan = serializers.CharField(read_only=True, allow_null=True)
    suspended = serializers.SerializerMethodField()
    deactivated = serializers.SerializerMethodField()
    onboarding_done = serializers.SerializerMethodField()

    class Meta:
        model = Restaurant
        fields = [
            "id",
            "name",
            "email",
            "phone",
            "country_code",
            "currency",
            "language",
            "timezone",
            "restaurant_type",
            "pos_provider",
            "pos_is_connected",
            "created_at",
            "updated_at",
            "staff_count",
            "subscription_status",
            "subscription_plan",
            "suspended",
            "deactivated",
            "onboarding_done",
        ]

    def get_suspended(self, obj):
        return tenant_is_suspended(obj.general_settings)

    def get_deactivated(self, obj):
        return tenant_is_deactivated(obj.general_settings)

    def get_onboarding_done(self, obj):
        return bool(obj.onboarding_completed_at)


class PlatformTenantDetailSerializer(PlatformTenantListSerializer):
    owner = serializers.SerializerMethodField()
    locations = serializers.SerializerMethodField()
    subscription = serializers.SerializerMethodField()
    staff = serializers.SerializerMethodField()
    recent_audit = serializers.SerializerMethodField()

    class Meta(PlatformTenantListSerializer.Meta):
        fields = PlatformTenantListSerializer.Meta.fields + [
            "address",
            "owner",
            "locations",
            "subscription",
            "staff",
            "recent_audit",
            "general_settings",
            "onboarding_completed_at",
        ]

    def get_owner(self, obj):
        owner = (
            User.objects.filter(restaurant=obj, role__in=["SUPER_ADMIN", "OWNER", "ADMIN"])
            .order_by("created_at")
            .first()
        )
        if not owner:
            return None
        return {
            "id": str(owner.id),
            "email": owner.email,
            "first_name": owner.first_name,
            "last_name": owner.last_name,
            "role": owner.role,
            "phone": owner.phone,
            "is_active": owner.is_active,
        }

    def get_locations(self, obj):
        return list(
            BusinessLocation.objects.filter(restaurant=obj).values(
                "id", "name", "is_primary", "is_active"
            )
        )

    def get_subscription(self, obj):
        try:
            sub = obj.subscription
        except Subscription.DoesNotExist:
            return None
        ops = sub.platform_ops if isinstance(sub.platform_ops, dict) else {}
        last_change = ops.get("last_plan_change") if isinstance(ops.get("last_plan_change"), dict) else None
        return {
            "id": sub.id,
            "status": sub.status,
            "plan": sub.plan.name if sub.plan else None,
            "plan_id": sub.plan_id,
            "tier": sub.plan.tier if sub.plan else None,
            "effective_tier": sub.effective_tier,
            "stripe_customer_id": sub.stripe_customer_id,
            "stripe_subscription_id": sub.stripe_subscription_id,
            "billing_interval": sub.billing_interval,
            "current_period_start": sub.current_period_start,
            "current_period_end": sub.current_period_end,
            "trial_ends_at": sub.trial_ends_at,
            "cancel_at_period_end": sub.cancel_at_period_end,
            "price_monthly": str(sub.plan.price_monthly) if sub.plan and sub.plan.price_monthly is not None else None,
            "last_plan_change": last_change,
        }

    def get_staff(self, obj):
        rows = (
            User.objects.filter(restaurant=obj)
            .order_by("role", "first_name")[:50]
        )
        return [
            {
                "id": str(u.id),
                "email": u.email,
                "first_name": u.first_name,
                "last_name": u.last_name,
                "role": u.role,
                "phone": u.phone,
                "is_active": u.is_active,
            }
            for u in rows
        ]

    def get_recent_audit(self, obj):
        logs = (
            AuditLog.objects.filter(restaurant=obj)
            .select_related("user")
            .order_by("-timestamp")[:15]
        )
        return [
            {
                "id": str(log.id),
                "timestamp": log.timestamp,
                "action_type": log.action_type,
                "description": log.description,
                "user_email": log.user.email if log.user_id else None,
            }
            for log in logs
        ]


class PlatformTenantWriteSerializer(serializers.ModelSerializer):
    suspended = serializers.BooleanField(required=False)
    deactivated = serializers.BooleanField(required=False)

    class Meta:
        model = Restaurant
        fields = [
            "name",
            "email",
            "phone",
            "address",
            "country_code",
            "currency",
            "language",
            "timezone",
            "restaurant_type",
            "suspended",
            "deactivated",
        ]

    def update(self, instance, validated_data):
        suspended = validated_data.pop("suspended", None)
        deactivated = validated_data.pop("deactivated", None)
        for k, v in validated_data.items():
            setattr(instance, k, v)

        gs = dict(instance.general_settings or {})
        will_be_suspended = (
            bool(suspended)
            if suspended is not None
            else tenant_is_suspended(gs)
        )

        if deactivated is True and not will_be_suspended and not tenant_is_deactivated(gs):
            raise serializers.ValidationError(
                {
                    "deactivated": (
                        "Suspend the tenant before deactivating accounts."
                    )
                }
            )

        if suspended is not None or deactivated is not None:
            if suspended is not None:
                gs["platform_suspended"] = bool(suspended)
            if deactivated is not None:
                gs["platform_deactivated"] = bool(deactivated)
            instance.general_settings = gs

        instance.save()

        # Deactivate / reactivate every login on this tenant.
        if deactivated is True:
            User.objects.filter(restaurant=instance).update(is_active=False)
        elif deactivated is False:
            User.objects.filter(restaurant=instance).update(is_active=True)

        return instance


class PlatformTenantCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    email = serializers.EmailField()
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    country_code = serializers.CharField(max_length=5, required=False, default="MA")
    currency = serializers.CharField(max_length=10, required=False, default="USD")
    owner_email = serializers.EmailField(required=False)
    owner_first_name = serializers.CharField(required=False, default="Owner")
    owner_last_name = serializers.CharField(required=False, default="")
    owner_password = serializers.CharField(required=False, write_only=True, min_length=8)


class PlatformUserSerializer(serializers.ModelSerializer):
    restaurant_name = serializers.CharField(source="restaurant.name", read_only=True, allow_null=True)
    is_locked = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "role",
            "phone",
            "is_active",
            "is_staff",
            "is_superuser",
            "is_platform_operator",
            "is_locked",
            "failed_login_attempts",
            "account_locked_until",
            "restaurant",
            "restaurant_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
            "restaurant_name",
            "is_locked",
            "failed_login_attempts",
            "account_locked_until",
            "is_platform_operator",
        ]

    def get_is_locked(self, obj):
        return bool(obj.is_account_locked())


class PlatformUserPatchSerializer(serializers.Serializer):
    is_active = serializers.BooleanField(required=False)
    is_staff = serializers.BooleanField(required=False)
    is_platform_operator = serializers.BooleanField(required=False)
    role = serializers.CharField(required=False)
    first_name = serializers.CharField(required=False, allow_blank=True)
    last_name = serializers.CharField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)


class PlatformOperatorPatchSerializer(serializers.Serializer):
    first_name = serializers.CharField(required=False, allow_blank=True)
    last_name = serializers.CharField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False)
    is_superuser = serializers.BooleanField(required=False)
    is_platform_operator = serializers.BooleanField(required=False)


class PlatformSubscriptionSerializer(serializers.ModelSerializer):
    restaurant_id = serializers.UUIDField(source="restaurant.id", read_only=True)
    restaurant_name = serializers.CharField(source="restaurant.name", read_only=True)
    plan_name = serializers.CharField(source="plan.name", read_only=True, allow_null=True)
    plan_tier = serializers.CharField(source="plan.tier", read_only=True, allow_null=True)

    class Meta:
        model = Subscription
        fields = [
            "id",
            "restaurant_id",
            "restaurant_name",
            "plan",
            "plan_name",
            "plan_tier",
            "status",
            "billing_interval",
            "stripe_customer_id",
            "stripe_subscription_id",
            "current_period_start",
            "current_period_end",
            "trial_ends_at",
            "cancel_at_period_end",
            "created_at",
            "updated_at",
        ]


class PlatformSubscriptionPatchSerializer(serializers.Serializer):
    plan = serializers.PrimaryKeyRelatedField(
        queryset=SubscriptionPlan.objects.filter(is_active=True),
        required=False,
        allow_null=False,
    )
    # Accepted only so we can reject with a clear error — status is system-owned.
    status = serializers.ChoiceField(choices=Subscription.STATUS_CHOICES, required=False)
    cancel_at_period_end = serializers.BooleanField(required=False)
    trial_ends_at = serializers.DateTimeField(required=False, allow_null=True)
    # Required when changing plan — stored on the subscription + audit log.
    reason = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class PlatformPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionPlan
        fields = [
            "id",
            "name",
            "slug",
            "tier",
            "price",
            "price_monthly",
            "price_yearly",
            "currency",
            "is_active",
            "feature_keys",
            "max_locations",
            "max_staff",
            "sort_order",
        ]


class PlatformAuditSerializer(serializers.ModelSerializer):
    user_email = serializers.CharField(source="user.email", read_only=True, allow_null=True)
    restaurant_name = serializers.CharField(source="restaurant.name", read_only=True, allow_null=True)

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "timestamp",
            "action_type",
            "entity_type",
            "entity_id",
            "description",
            "user_email",
            "restaurant",
            "restaurant_name",
            "metadata",
        ]
