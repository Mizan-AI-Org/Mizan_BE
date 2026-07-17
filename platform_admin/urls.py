from django.urls import path
from . import views

urlpatterns = [
    path("me/", views.platform_me, name="platform_me"),
    path("overview/", views.platform_overview, name="platform_overview"),
    path("tenants/", views.platform_tenants, name="platform_tenants"),
    path("tenants/<uuid:tenant_id>/", views.platform_tenant_detail, name="platform_tenant_detail"),
    path("users/", views.platform_users, name="platform_users"),
    path("users/<uuid:user_id>/", views.platform_user_detail, name="platform_user_detail"),
    path("users/<uuid:user_id>/unlock/", views.platform_user_unlock, name="platform_user_unlock"),
    path(
        "users/<uuid:user_id>/reset-password/",
        views.platform_user_reset_password,
        name="platform_user_reset_password",
    ),
    path("operators/", views.platform_operators, name="platform_operators"),
    path("billing/plans/", views.platform_billing_plans, name="platform_billing_plans"),
    path(
        "billing/subscriptions/",
        views.platform_billing_subscriptions,
        name="platform_billing_subscriptions",
    ),
    path(
        "billing/subscriptions/<int:sub_id>/",
        views.platform_billing_subscription_detail,
        name="platform_billing_subscription_detail",
    ),
    path("health/", views.platform_health, name="platform_health"),
    path("audit/", views.platform_audit, name="platform_audit"),
    path("impersonate/", views.platform_impersonate, name="platform_impersonate"),
]
