from django.urls import path
from . import views
from . import whatsapp_views

urlpatterns = [
    path("auth/login/", views.platform_ops_login, name="platform_ops_login"),
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
    path(
        "operators/<uuid:user_id>/",
        views.platform_operator_detail,
        name="platform_operator_detail",
    ),
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
    path("whatsapp/config/", whatsapp_views.platform_whatsapp_config, name="platform_whatsapp_config"),
    path("whatsapp/config/test/", whatsapp_views.platform_whatsapp_test, name="platform_whatsapp_test"),
    path(
        "whatsapp/config/disconnect/",
        whatsapp_views.platform_whatsapp_disconnect,
        name="platform_whatsapp_disconnect",
    ),
    path("whatsapp/templates/", whatsapp_views.platform_whatsapp_templates, name="platform_whatsapp_templates"),
    path(
        "whatsapp/templates/sync/",
        whatsapp_views.platform_whatsapp_templates_sync,
        name="platform_whatsapp_templates_sync",
    ),
    path(
        "whatsapp/templates/<int:template_id>/",
        whatsapp_views.platform_whatsapp_template_detail,
        name="platform_whatsapp_template_detail",
    ),
]
