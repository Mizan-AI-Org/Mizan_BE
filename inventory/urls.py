from django.urls import path, include
from .views import (
    InventoryItemListCreateAPIView,
    InventoryItemRetrieveUpdateDestroyAPIView,
    SupplierListCreateAPIView,
    SupplierRetrieveUpdateDestroyAPIView,
    PurchaseOrderListCreateAPIView,
    PurchaseOrderRetrieveUpdateDestroyAPIView,
    PurchaseOrderItemListCreateAPIView,
    PurchaseOrderItemRetrieveUpdateDestroyAPIView,
    StockAdjustmentListCreateAPIView,
    StockAdjustmentRetrieveUpdateDestroyAPIView,
)
from .views_agent import agent_list_inventory_items
from . import views_agent_morocco as morocco

urlpatterns = [
    # Inventory Items
    path('items/', InventoryItemListCreateAPIView.as_view(), name='inventory-item-list-create'),
    path('agent/items/', agent_list_inventory_items, name='agent_inventory_items'),

    # Waste Reporting (Miya)
    path('agent/waste/', morocco.agent_report_waste, name='agent-report-waste'),
    path('agent/waste/summary/', morocco.agent_waste_summary, name='agent-waste-summary'),

    # Inventory Counts (Miya)
    path('agent/count/start/', morocco.agent_start_inventory_count, name='agent-start-count'),
    path('agent/count/item/', morocco.agent_count_item, name='agent-count-item'),

    # Supplier WhatsApp Ordering (Miya)
    path('agent/supplier-order/', morocco.agent_send_supplier_order, name='agent-supplier-order'),
    path('items/<uuid:pk>/', InventoryItemRetrieveUpdateDestroyAPIView.as_view(), name='inventory-item-detail'),

    # Suppliers
    path('suppliers/', SupplierListCreateAPIView.as_view(), name='supplier-list-create'),
    path('suppliers/<uuid:pk>/', SupplierRetrieveUpdateDestroyAPIView.as_view(), name='supplier-detail'),

    # Purchase Orders
    path('purchase-orders/', PurchaseOrderListCreateAPIView.as_view(), name='purchase-order-list-create'),
    path('purchase-orders/<uuid:pk>/', PurchaseOrderRetrieveUpdateDestroyAPIView.as_view(), name='purchase-order-detail'),

    # Purchase Order Items (nested under purchase orders)
    path('purchase-orders/<uuid:purchase_order_pk>/items/', PurchaseOrderItemListCreateAPIView.as_view(), name='purchase-order-item-list-create'),
    path('purchase-orders/<uuid:purchase_order_pk>/items/<uuid:pk>/', PurchaseOrderItemRetrieveUpdateDestroyAPIView.as_view(), name='purchase-order-item-detail'),

    # Stock Adjustments
    path('stock-adjustments/', StockAdjustmentListCreateAPIView.as_view(), name='stock-adjustment-list-create'),
    path('stock-adjustments/<uuid:pk>/', StockAdjustmentRetrieveUpdateDestroyAPIView.as_view(), name='stock-adjustment-detail'),
]
