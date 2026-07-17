"""Platform ops APIs for internal Mizan operators (is_staff)."""
from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.functions import TruncWeek, TruncMonth
from django.db.models import Count, Q, OuterRef, Subquery, CharField, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import Restaurant, AuditLog, CustomUser
from billing.models import Subscription, SubscriptionPlan
from .permissions import IsPlatformOperator, IsPlatformSuperuser, user_is_platform_superuser
from .serializers import (
    PlatformTenantListSerializer,
    PlatformTenantDetailSerializer,
    PlatformTenantWriteSerializer,
    PlatformTenantCreateSerializer,
    PlatformUserSerializer,
    PlatformUserPatchSerializer,
    PlatformSubscriptionSerializer,
    PlatformSubscriptionPatchSerializer,
    PlatformPlanSerializer,
    PlatformAuditSerializer,
)

logger = logging.getLogger(__name__)
User = get_user_model()

PRIVILEGED_ROLES = ("SUPER_ADMIN", "OWNER", "ADMIN")


def _growth_series(qs, date_field: str, period: str, buckets: int, now):
    """New + cumulative counts per week or month bucket."""
    if period == "week":
        trunc = TruncWeek(date_field)
        start = (now - timedelta(days=7 * (buckets - 1))).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start = start - timedelta(days=start.weekday())
    else:
        trunc = TruncMonth(date_field)
        y, m = now.year, now.month
        m -= buckets - 1
        while m <= 0:
            m += 12
            y -= 1
        start = now.replace(year=y, month=m, day=1, hour=0, minute=0, second=0, microsecond=0)

    baseline = qs.filter(**{f"{date_field}__lt": start}).count()
    raw_rows = (
        qs.filter(**{f"{date_field}__gte": start})
        .annotate(bucket=trunc)
        .values("bucket")
        .annotate(new=Count("id"))
        .order_by("bucket")
    )
    raw: dict[str, int] = {}
    for row in raw_rows:
        b = row["bucket"]
        if not b:
            continue
        if period == "week":
            # ISO week key
            key = f"{b.isocalendar().year}-W{b.isocalendar().week:02d}"
        else:
            key = f"{b.year}-{b.month:02d}"
        raw[key] = int(row["new"])

    points = []
    running = baseline
    if period == "week":
        cursor = start
        for _ in range(buckets):
            iso = cursor.isocalendar()
            key = f"{iso.year}-W{iso.week:02d}"
            new = raw.get(key, 0)
            running += new
            points.append(
                {
                    "date": cursor.date().isoformat(),
                    "label": cursor.strftime("%b %d"),
                    "new": new,
                    "cumulative": running,
                }
            )
            cursor = cursor + timedelta(days=7)
    else:
        cursor = start
        for _ in range(buckets):
            key = f"{cursor.year}-{cursor.month:02d}"
            new = raw.get(key, 0)
            running += new
            points.append(
                {
                    "date": cursor.date().isoformat(),
                    "label": cursor.strftime("%b %Y"),
                    "new": new,
                    "cumulative": running,
                }
            )
            ny, nm = cursor.year, cursor.month + 1
            if nm > 12:
                nm, ny = 1, ny + 1
            cursor = cursor.replace(year=ny, month=nm, day=1)

    return points


def _log_platform_action(request, action_type, entity_type, entity_id, description, new_values=None):
    try:
        AuditLog.objects.create(
            restaurant=None,
            user=request.user,
            action_type=action_type,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id else "",
            description=description,
            new_values=new_values or {},
            metadata={"platform_ops": True, "path": request.path},
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:500],
        )
    except Exception:
        logger.exception("Failed to write platform audit log")


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsPlatformOperator])
def platform_me(request):
    u = request.user
    from .permissions import user_is_platform_operator, user_is_platform_superuser

    return Response(
        {
            "id": str(u.id),
            "email": u.email,
            "first_name": u.first_name,
            "last_name": u.last_name,
            "is_staff": bool(getattr(u, "is_staff", False) or user_is_platform_operator(u)),
            "is_superuser": user_is_platform_superuser(u),
            "is_platform_operator": user_is_platform_operator(u),
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsPlatformOperator])
def platform_overview(request):
    now = timezone.now()
    week = now + timedelta(days=7)

    restaurants = Restaurant.objects.count()
    # Individuals belonging to establishments (not the tenant/restaurant records).
    users_in_tenants = User.objects.filter(is_active=True, restaurant__isnull=False)
    users_active = users_in_tenants.count()
    staff_active = users_in_tenants.exclude(role__in=PRIVILEGED_ROLES).count()
    managers_active = users_in_tenants.filter(role__in=PRIVILEGED_ROLES).count()

    sub_qs = Subscription.objects.all()
    by_status = {
        row["status"]: row["c"]
        for row in sub_qs.values("status").annotate(c=Count("id"))
    }
    trials_ending = sub_qs.filter(
        status="trialing",
        trial_ends_at__gte=now,
        trial_ends_at__lte=week,
    ).count()
    active_paid = sub_qs.filter(status__in=["active", "trialing"]).count()

    # Rough MRR from active monthly plans
    mrr = 0.0
    for sub in sub_qs.filter(status="active").select_related("plan"):
        if not sub.plan:
            continue
        if (sub.billing_interval or "month") == "year":
            price = float(sub.plan.price_yearly or 0) / 12.0
        else:
            price = float(sub.plan.price_monthly or sub.plan.price or 0)
        mrr += price

    wa_ok = bool(
        (getattr(settings, "WHATSAPP_ACCESS_TOKEN", None) or "").strip()
        and (getattr(settings, "WHATSAPP_PHONE_NUMBER_ID", None) or "").strip()
    )
    stripe_key = (getattr(settings, "STRIPE_SECRET_KEY", None) or "").strip()
    stripe_ok = bool(stripe_key) and not stripe_key.lower().startswith("your-")
    lua_ok = bool((getattr(settings, "LUA_WHATSAPP_WEBHOOK_URL", None) or "").strip())

    user_qs = User.objects.filter(restaurant__isnull=False)
    tenant_qs = Restaurant.objects.all()
    growth = {
        "weekly": {
            "users": _growth_series(user_qs, "date_joined", "week", 12, now),
            "tenants": _growth_series(tenant_qs, "created_at", "week", 12, now),
        },
        "monthly": {
            "users": _growth_series(user_qs, "date_joined", "month", 12, now),
            "tenants": _growth_series(tenant_qs, "created_at", "month", 12, now),
        },
    }

    # Week-over-week / month-over-month deltas for KPI cards
    users_weekly = growth["weekly"]["users"]
    tenants_weekly = growth["weekly"]["tenants"]
    users_wow = (users_weekly[-1]["new"] - users_weekly[-2]["new"]) if len(users_weekly) >= 2 else 0
    tenants_wow = (tenants_weekly[-1]["new"] - tenants_weekly[-2]["new"]) if len(tenants_weekly) >= 2 else 0

    return Response(
        {
            "restaurants": restaurants,
            "users_active": users_active,
            "staff_active": staff_active,
            "managers_active": managers_active,
            "subscriptions_by_status": by_status,
            "subscriptions_active": active_paid,
            "trials_ending_7d": trials_ending,
            "mrr_estimate": round(mrr, 2),
            "deltas": {
                "users_wow": users_wow,
                "tenants_wow": tenants_wow,
                "users_new_this_week": users_weekly[-1]["new"] if users_weekly else 0,
                "tenants_new_this_week": tenants_weekly[-1]["new"] if tenants_weekly else 0,
            },
            "growth": growth,
            "health": {
                "whatsapp_configured": wa_ok,
                "lua_webhook_configured": lua_ok,
            },
            "payments": {
                "stripe_available": stripe_ok,
                "note": (
                    "Payment provider is chosen per tenant based on registered "
                    "location/country. Stripe is one option."
                ),
            },
        }
    )


def _tenant_queryset():
    sub_status = Subscription.objects.filter(restaurant_id=OuterRef("pk")).values("status")[:1]
    sub_plan = Subscription.objects.filter(restaurant_id=OuterRef("pk")).values("plan__name")[:1]
    return Restaurant.objects.annotate(
        staff_count=Count("staff", distinct=True),
        subscription_status=Subquery(sub_status, output_field=CharField()),
        subscription_plan=Subquery(sub_plan, output_field=CharField()),
    ).order_by("-created_at")


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, IsPlatformOperator])
def platform_tenants(request):
    if request.method == "GET":
        qs = _tenant_queryset()
        q = (request.query_params.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(name__icontains=q)
                | Q(email__icontains=q)
                | Q(phone__icontains=q)
            )
        country = (request.query_params.get("country") or "").strip()
        if country:
            qs = qs.filter(country_code__iexact=country)
        pos = (request.query_params.get("pos") or "").strip()
        if pos:
            qs = qs.filter(pos_provider__iexact=pos)
        onboarding = (request.query_params.get("onboarding") or "").strip().lower()
        if onboarding == "done":
            qs = qs.exclude(onboarding_completed_at__isnull=True)
        elif onboarding == "pending":
            qs = qs.filter(onboarding_completed_at__isnull=True)
        suspended = (request.query_params.get("suspended") or "").strip().lower()
        if suspended == "1" or suspended == "true":
            qs = qs.filter(general_settings__platform_suspended=True)
        elif suspended == "0" or suspended == "false":
            qs = qs.exclude(general_settings__platform_suspended=True)

        try:
            limit = min(int(request.query_params.get("page_size") or 50), 200)
        except ValueError:
            limit = 50
        try:
            page = max(int(request.query_params.get("page") or 1), 1)
        except ValueError:
            page = 1
        total = qs.count()
        start = (page - 1) * limit
        rows = qs[start : start + limit]
        return Response(
            {
                "count": total,
                "page": page,
                "page_size": limit,
                "results": PlatformTenantListSerializer(rows, many=True).data,
            }
        )

    # POST create
    ser = PlatformTenantCreateSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    data = ser.validated_data
    if Restaurant.objects.filter(email__iexact=data["email"]).exists():
        return Response({"error": "Restaurant email already exists"}, status=400)

    restaurant = Restaurant.objects.create(
        name=data["name"],
        email=data["email"],
        phone=data.get("phone") or "",
        country_code=data.get("country_code") or "MA",
        currency=data.get("currency") or "USD",
    )
    owner_email = data.get("owner_email") or data["email"]
    if not User.objects.filter(email__iexact=owner_email).exists():
        owner = User(
            email=owner_email,
            first_name=data.get("owner_first_name") or "Owner",
            last_name=data.get("owner_last_name") or "",
            role="SUPER_ADMIN",
            restaurant=restaurant,
            is_active=True,
            is_staff=False,
            is_superuser=False,
        )
        pwd = data.get("owner_password") or User.objects.make_random_password()
        owner.set_password(pwd)
        owner.save()
    from billing.services import ensure_starter_subscription

    ensure_starter_subscription(restaurant)
    _log_platform_action(
        request,
        "CREATE",
        "Restaurant",
        restaurant.id,
        f"Created tenant {restaurant.name}",
        {"email": restaurant.email},
    )
    detail = _tenant_queryset().filter(pk=restaurant.pk).first()
    return Response(
        PlatformTenantDetailSerializer(detail).data,
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated, IsPlatformOperator])
def platform_tenant_detail(request, tenant_id):
    try:
        restaurant = Restaurant.objects.get(pk=tenant_id)
    except Restaurant.DoesNotExist:
        return Response({"error": "Tenant not found"}, status=404)

    if request.method == "GET":
        detail = _tenant_queryset().filter(pk=restaurant.pk).first()
        return Response(PlatformTenantDetailSerializer(detail).data)

    ser = PlatformTenantWriteSerializer(restaurant, data=request.data, partial=True)
    ser.is_valid(raise_exception=True)
    ser.save()
    _log_platform_action(
        request,
        "UPDATE",
        "Restaurant",
        restaurant.id,
        f"Updated tenant {restaurant.name}",
        dict(request.data),
    )
    detail = _tenant_queryset().filter(pk=restaurant.pk).first()
    return Response(PlatformTenantDetailSerializer(detail).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsPlatformOperator])
def platform_users(request):
    # Tenant-side people only — platform operators live under /admin/operators.
    qs = (
        User.objects.filter(is_platform_operator=False)
        .select_related("restaurant")
        .order_by("-created_at")
    )
    q = (request.query_params.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(email__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(phone__icontains=q)
        )
    restaurant_id = (request.query_params.get("restaurant_id") or "").strip()
    if restaurant_id:
        qs = qs.filter(restaurant_id=restaurant_id)
    role = (request.query_params.get("role") or "").strip()
    if role:
        qs = qs.filter(role__iexact=role)
    active = (request.query_params.get("is_active") or "").strip().lower()
    if active in ("1", "true"):
        qs = qs.filter(is_active=True)
    elif active in ("0", "false"):
        qs = qs.filter(is_active=False)

    try:
        limit = min(int(request.query_params.get("page_size") or 50), 200)
    except ValueError:
        limit = 50
    try:
        page = max(int(request.query_params.get("page") or 1), 1)
    except ValueError:
        page = 1
    total = qs.count()
    start = (page - 1) * limit
    rows = qs[start : start + limit]
    return Response(
        {
            "count": total,
            "page": page,
            "page_size": limit,
            "results": PlatformUserSerializer(rows, many=True).data,
        }
    )


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated, IsPlatformOperator])
def platform_user_detail(request, user_id):
    try:
        user = User.objects.select_related("restaurant").get(pk=user_id)
    except User.DoesNotExist:
        return Response({"error": "User not found"}, status=404)

    if request.method == "GET":
        return Response(PlatformUserSerializer(user).data)

    ser = PlatformUserPatchSerializer(data=request.data, partial=True)
    ser.is_valid(raise_exception=True)
    data = ser.validated_data

    if "is_staff" in data:
        if not user_is_platform_superuser(request.user):
            return Response(
                {"error": "Only platform superusers can change is_staff"},
                status=403,
            )
        user.is_staff = bool(data["is_staff"])
        if not user.is_staff:
            user.is_superuser = False
            user.is_platform_operator = False

    if "is_platform_operator" in data:
        if not user_is_platform_superuser(request.user):
            return Response(
                {"error": "Only platform superusers can change platform operator access"},
                status=403,
            )
        user.is_platform_operator = bool(data["is_platform_operator"])
        if user.is_platform_operator:
            user.is_staff = True
        # Revoking ops access does not force-clear Django is_staff (may still need API /admin)

    if "is_active" in data:
        user.is_active = bool(data["is_active"])
    if "role" in data:
        user.role = data["role"]
    if "first_name" in data:
        user.first_name = data["first_name"]
    if "last_name" in data:
        user.last_name = data["last_name"]
    if "phone" in data:
        user.phone = data["phone"]
    user.save()
    _log_platform_action(
        request,
        "UPDATE",
        "CustomUser",
        user.id,
        f"Updated user {user.email}",
        dict(request.data),
    )
    return Response(PlatformUserSerializer(user).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsPlatformOperator])
def platform_user_unlock(request, user_id):
    """Clear lockout after failed password/PIN retries."""
    try:
        user = User.objects.select_related("restaurant").get(pk=user_id)
    except User.DoesNotExist:
        return Response({"error": "User not found"}, status=404)

    user.failed_login_attempts = 0
    user.account_locked_until = None
    user.save(update_fields=["failed_login_attempts", "account_locked_until"])
    _log_platform_action(
        request,
        "ACCOUNT_UNLOCKED",
        "CustomUser",
        user.id,
        f"Unlocked account for {user.email}",
    )
    return Response(PlatformUserSerializer(user).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsPlatformOperator])
def platform_user_reset_password(request, user_id):
    """Set a new password for a user (ops support)."""
    try:
        user = User.objects.select_related("restaurant").get(pk=user_id)
    except User.DoesNotExist:
        return Response({"error": "User not found"}, status=404)

    new_password = (request.data.get("password") or "").strip()
    if not new_password:
        return Response({"error": "password is required"}, status=400)
    if len(new_password) < 8:
        return Response({"error": "Password must be at least 8 characters"}, status=400)

    try:
        from django.core.exceptions import ValidationError

        user.validate_password_complexity(new_password)
    except ValidationError as exc:
        msg = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        return Response({"error": msg}, status=400)

    user.set_password(new_password)
    # Clearing lockout so they can sign in immediately with the new password
    user.failed_login_attempts = 0
    user.account_locked_until = None
    user.save()
    _log_platform_action(
        request,
        "PASSWORD_CHANGED",
        "CustomUser",
        user.id,
        f"Password reset for {user.email} by platform operator",
    )
    return Response(
        {
            "message": "Password reset successfully",
            "user": PlatformUserSerializer(user).data,
        }
    )


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, IsPlatformOperator])
def platform_operators(request):
    """List / create Mizan platform operator accounts (is_staff)."""
    if request.method == "GET":
        qs = (
            User.objects.filter(is_platform_operator=True)
            .select_related("restaurant")
            .order_by("-is_superuser", "email")
        )
        return Response(
            {
                "count": qs.count(),
                "results": PlatformUserSerializer(qs, many=True).data,
            }
        )

    # Create — superuser only (must already be a platform operator)
    if not user_is_platform_superuser(request.user):
        return Response(
            {"error": "Only platform superusers can create operators"},
            status=403,
        )

    email = (request.data.get("email") or "").strip().lower()
    first_name = (request.data.get("first_name") or "").strip() or "Ops"
    last_name = (request.data.get("last_name") or "").strip() or "Admin"
    password = (request.data.get("password") or "").strip()
    make_super = bool(request.data.get("is_superuser"))

    if not email:
        return Response({"error": "email is required"}, status=400)
    if not password or len(password) < 8:
        return Response({"error": "password must be at least 8 characters"}, status=400)

    existing = User.objects.filter(email__iexact=email).first()
    if existing:
        if existing.is_platform_operator:
            return Response({"error": "User is already a platform operator"}, status=400)
        # Promote to dedicated ops — detach from restaurant so they are not a tenant admin dual-hat
        existing.is_staff = True
        existing.is_platform_operator = True
        if make_super:
            existing.is_superuser = True
        existing.restaurant = None
        existing.set_password(password)
        existing.failed_login_attempts = 0
        existing.account_locked_until = None
        existing.is_active = True
        if not existing.is_admin_role():
            existing.role = "SUPER_ADMIN"
        try:
            from django.core.exceptions import ValidationError

            existing.validate_password_complexity(password)
        except ValidationError as exc:
            msg = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            return Response({"error": msg}, status=400)
        existing.save()
        _log_platform_action(
            request,
            "PERMISSION_CHANGE",
            "CustomUser",
            existing.id,
            f"Promoted {existing.email} to platform operator",
            {"is_superuser": existing.is_superuser},
        )
        return Response(PlatformUserSerializer(existing).data, status=status.HTTP_201_CREATED)

    user = User(
        email=email,
        first_name=first_name,
        last_name=last_name,
        role="SUPER_ADMIN",
        restaurant=None,
        is_active=True,
        is_staff=True,
        is_platform_operator=True,
        is_superuser=make_super,
        is_verified=True,
    )
    try:
        from django.core.exceptions import ValidationError

        user.validate_password_complexity(password)
    except ValidationError as exc:
        msg = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        return Response({"error": msg}, status=400)
    user.set_password(password)
    user.save()
    _log_platform_action(
        request,
        "CREATE",
        "CustomUser",
        user.id,
        f"Created platform operator {user.email}",
        {"is_superuser": user.is_superuser},
    )
    return Response(PlatformUserSerializer(user).data, status=status.HTTP_201_CREATED)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsPlatformOperator])
def platform_billing_plans(request):
    qs = SubscriptionPlan.objects.all().order_by("sort_order", "name")
    return Response(PlatformPlanSerializer(qs, many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsPlatformOperator])
def platform_billing_subscriptions(request):
    qs = Subscription.objects.select_related("restaurant", "plan").order_by("-updated_at")
    status_f = (request.query_params.get("status") or "").strip()
    if status_f:
        qs = qs.filter(status=status_f)
    q = (request.query_params.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(restaurant__name__icontains=q) | Q(restaurant__email__icontains=q)
        )
    try:
        limit = min(int(request.query_params.get("page_size") or 50), 200)
    except ValueError:
        limit = 50
    try:
        page = max(int(request.query_params.get("page") or 1), 1)
    except ValueError:
        page = 1
    total = qs.count()
    start = (page - 1) * limit
    rows = qs[start : start + limit]
    return Response(
        {
            "count": total,
            "page": page,
            "page_size": limit,
            "results": PlatformSubscriptionSerializer(rows, many=True).data,
        }
    )


@api_view(["PATCH"])
@permission_classes([IsAuthenticated, IsPlatformOperator])
def platform_billing_subscription_detail(request, sub_id):
    try:
        sub = Subscription.objects.select_related("restaurant", "plan").get(pk=sub_id)
    except Subscription.DoesNotExist:
        return Response({"error": "Subscription not found"}, status=404)

    ser = PlatformSubscriptionPatchSerializer(data=request.data, partial=True)
    ser.is_valid(raise_exception=True)
    data = ser.validated_data
    if "plan" in data:
        sub.plan = data["plan"]
    if "status" in data:
        sub.status = data["status"]
    if "cancel_at_period_end" in data:
        sub.cancel_at_period_end = data["cancel_at_period_end"]
    if "trial_ends_at" in data:
        sub.trial_ends_at = data["trial_ends_at"]
    sub.save()
    _log_platform_action(
        request,
        "UPDATE",
        "Subscription",
        sub.id,
        f"Updated subscription for {sub.restaurant.name}",
        dict(request.data),
    )
    return Response(PlatformSubscriptionSerializer(sub).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsPlatformOperator])
def platform_health(request):
    """Platform dependency status for operators.

    ``ok`` / Degraded is driven only by platform-wide dependencies (messaging,
    cache). Payment providers are optional and tenant/country-specific — a
    missing Stripe key must not mark the whole platform as degraded.
    """
    redis_ok = False
    redis_error = None
    try:
        from django.core.cache import cache

        cache.set("platform_health_ping", "1", 5)
        redis_ok = cache.get("platform_health_ping") == "1"
    except Exception as exc:
        redis_error = str(exc)[:200]

    wa_token = bool((getattr(settings, "WHATSAPP_ACCESS_TOKEN", None) or "").strip())
    wa_phone_id = bool((getattr(settings, "WHATSAPP_PHONE_NUMBER_ID", None) or "").strip())
    activation_digits = "".join(
        filter(
            str.isdigit,
            str(getattr(settings, "WHATSAPP_ACTIVATION_WA_PHONE", "") or ""),
        )
    )
    wa_activation = bool(activation_digits)
    stripe_key = (getattr(settings, "STRIPE_SECRET_KEY", None) or "").strip()
    stripe_ok = bool(stripe_key) and not stripe_key.lower().startswith("your-")
    lua_url = (getattr(settings, "LUA_WHATSAPP_WEBHOOK_URL", None) or "").strip()

    # Required platform checks — these drive Overall Healthy / Degraded.
    items = [
        {
            "id": "whatsapp_access_token",
            "label": "WhatsApp access token",
            "ok": wa_token,
            "kind": "config",
            "required": True,
            "message": (
                "WHATSAPP_ACCESS_TOKEN is set"
                if wa_token
                else "WHATSAPP_ACCESS_TOKEN is not set"
            ),
        },
        {
            "id": "whatsapp_phone_number_id",
            "label": "WhatsApp phone number ID",
            "ok": wa_phone_id,
            "kind": "config",
            "required": True,
            "message": (
                "WHATSAPP_PHONE_NUMBER_ID is set"
                if wa_phone_id
                else "WHATSAPP_PHONE_NUMBER_ID is not set"
            ),
        },
        {
            "id": "whatsapp_activation_wa_phone",
            "label": "WhatsApp activation number",
            "ok": wa_activation,
            "kind": "config",
            "required": True,
            "message": (
                f"Activation phone configured ({activation_digits})"
                if wa_activation
                else "WHATSAPP_ACTIVATION_WA_PHONE is not set"
            ),
        },
        {
            "id": "lua_whatsapp_webhook",
            "label": "Lua WhatsApp webhook",
            "ok": bool(lua_url),
            "kind": "config",
            "required": True,
            "message": (
                "LUA_WHATSAPP_WEBHOOK_URL is set"
                if lua_url
                else "LUA_WHATSAPP_WEBHOOK_URL is not set"
            ),
        },
        {
            "id": "redis",
            "label": "Redis / cache",
            "ok": redis_ok,
            "kind": "runtime",
            "required": True,
            "message": (
                "Cache ping succeeded"
                if redis_ok
                else (redis_error or "Cache ping failed — check Redis is running")
            ),
        },
        # Optional — does not affect overall status.
        {
            "id": "stripe_configured",
            "label": "Stripe (optional)",
            "ok": stripe_ok,
            "kind": "optional",
            "required": False,
            "message": (
                "Stripe keys are available for tenants that use Stripe"
                if stripe_ok
                else "Not configured — fine if you use another provider per tenant/country"
            ),
        },
    ]

    checks = {item["id"]: item["ok"] for item in items}
    failed_required = [item for item in items if item.get("required", True) and not item["ok"]]

    return Response(
        {
            "ok": len(failed_required) == 0,
            "status": "ok" if not failed_required else "degraded",
            "summary": (
                "Required platform services look healthy"
                if not failed_required
                else f"{len(failed_required)} issue{'s' if len(failed_required) != 1 else ''}: "
                + ", ".join(item["label"] for item in failed_required)
            ),
            "checks": checks,
            "items": items,
            "payments": {
                "note": (
                    "Payment provider is chosen per tenant based on registered "
                    "location/country. Stripe is one option — missing Stripe keys "
                    "do not degrade overall platform health."
                ),
                "stripe_available": stripe_ok,
            },
            "details": {
                "lua_webhook_url_set": bool(lua_url),
                "redis_error": redis_error,
                "activation_phone_digits": activation_digits or None,
                "debug": bool(getattr(settings, "DEBUG", False)),
            },
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsPlatformOperator])
def platform_audit(request):
    qs = AuditLog.objects.select_related("user", "restaurant").order_by("-timestamp")
    platform_only = (request.query_params.get("platform_only") or "").strip().lower()
    if platform_only in ("1", "true"):
        qs = qs.filter(metadata__platform_ops=True)
    restaurant_id = (request.query_params.get("restaurant_id") or "").strip()
    if restaurant_id:
        qs = qs.filter(restaurant_id=restaurant_id)
    try:
        limit = min(int(request.query_params.get("page_size") or 25), 200)
    except ValueError:
        limit = 25
    try:
        page = max(int(request.query_params.get("page") or 1), 1)
    except ValueError:
        page = 1
    total = qs.count()
    start = (page - 1) * limit
    rows = qs[start : start + limit]
    return Response(
        {
            "count": total,
            "page": page,
            "page_size": limit,
            "results": PlatformAuditSerializer(rows, many=True).data,
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsPlatformOperator])
def platform_impersonate(request):
    """Issue JWT for a privileged user of the target restaurant (support access)."""
    restaurant_id = request.data.get("restaurant_id")
    if not restaurant_id:
        return Response({"error": "restaurant_id required"}, status=400)
    try:
        restaurant = Restaurant.objects.get(pk=restaurant_id)
    except Restaurant.DoesNotExist:
        return Response({"error": "Tenant not found"}, status=404)

    gs = restaurant.general_settings or {}
    if gs.get("platform_suspended"):
        return Response({"error": "Tenant is suspended"}, status=400)

    target = (
        User.objects.filter(
            restaurant=restaurant,
            is_active=True,
            role__in=PRIVILEGED_ROLES,
        )
        .order_by("created_at")
        .first()
    )
    if not target:
        return Response(
            {"error": "No active privileged user on this tenant to impersonate"},
            status=400,
        )

    refresh = RefreshToken.for_user(target)
    # Mark token so FE can show banner / backend can detect support session
    refresh["impersonated_by"] = str(request.user.id)
    refresh["impersonation"] = True
    refresh["restaurant_id"] = str(restaurant.id)
    access = refresh.access_token
    access["impersonated_by"] = str(request.user.id)
    access["impersonation"] = True
    access["restaurant_id"] = str(restaurant.id)

    _log_platform_action(
        request,
        "OTHER",
        "Impersonation",
        restaurant.id,
        f"Impersonated {target.email} on tenant {restaurant.name}",
        {"target_user_id": str(target.id)},
    )

    return Response(
        {
            "access": str(access),
            "refresh": str(refresh),
            "user": {
                "id": str(target.id),
                "email": target.email,
                "first_name": target.first_name,
                "last_name": target.last_name,
                "role": target.role,
                "restaurant_id": str(restaurant.id),
                "restaurant_name": restaurant.name,
            },
            "restaurant": {
                "id": str(restaurant.id),
                "name": restaurant.name,
            },
            "impersonated_by": {
                "id": str(request.user.id),
                "email": request.user.email,
            },
        }
    )
