from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    TableViewSet, OrderViewSet, OrderLineItemViewSet,
    PaymentViewSet, POSTransactionViewSet, ReceiptSettingViewSet,
    DiscountViewSet
)
from . import webhooks
from . import views_agent
from . import views_oauth

router = DefaultRouter()
router.register(r'tables', TableViewSet, basename='pos-table')
router.register(r'orders', OrderViewSet, basename='pos-order')
router.register(r'line-items', OrderLineItemViewSet, basename='pos-line-item')
router.register(r'payments', PaymentViewSet, basename='pos-payment')
router.register(r'transactions', POSTransactionViewSet, basename='pos-transaction')
router.register(r'receipt-settings', ReceiptSettingViewSet, basename='pos-receipt-setting')
router.register(r'discounts', DiscountViewSet, basename='pos-discount')

urlpatterns = [
    path('', include(router.urls)),
    
    # Integration Management
    path('sync/menu/', webhooks.sync_menu_view, name='pos-sync-menu'),
    path('sync/orders/', webhooks.sync_orders_view, name='pos-sync-orders'),
    
    # Webhooks
    path('webhooks/toast/', webhooks.TOASTWebhookView.as_view(), name='toast-webhook'),
    path('webhooks/square/', webhooks.SquareWebhookView.as_view(), name='square-webhook'),
    path('webhooks/square/<uuid:restaurant_id>/', webhooks.SquareWebhookTenantView.as_view(), name='square-webhook-tenant'),
   path('webhooks/clover/', webhooks.CloverWebhookView.as_view(), name='clover-webhook'),

    # Agent (Lua) integration
    path('agent/sync/menu/', views_agent.agent_sync_menu, name='agent-pos-sync-menu'),
    path('agent/sync/orders/', views_agent.agent_sync_orders, name='agent-pos-sync-orders'),
    path('agent/external/', views_agent.agent_get_external_objects, name='agent-pos-external-objects'),
    path('agent/sales-summary/', views_agent.agent_get_pos_sales_summary, name='agent-pos-sales-summary'),
    path('agent/top-items/', views_agent.agent_get_top_items, name='agent-pos-top-items'),
    path('agent/status/', views_agent.agent_get_pos_status, name='agent-pos-status'),
    path('agent/sales-analysis/', views_agent.agent_get_sales_analysis, name='agent-pos-sales-analysis'),
    path('agent/prep-list/', views_agent.agent_get_prep_list, name='agent-pos-prep-list'),

    # Square OAuth connect/disconnect
    path('square/authorize/', views_oauth.square_authorize, name='square-authorize'),
    path('square/callback/', views_oauth.square_callback, name='square-callback'),
    path('square/disconnect/', views_oauth.square_disconnect, name='square-disconnect'),
    path('connection-status/', views_oauth.pos_connection_status, name='pos-connection-status'),
]