from django.urls import path, include
from .views import (
    TableListCreateAPIView,
    TableRetrieveUpdateDestroyAPIView,
    OrderListCreateAPIView,
    OrderRetrieveUpdateDestroyAPIView,
    OrderStatusUpdateAPIView,
    OrderItemListCreateAPIView,
    OrderItemRetrieveUpdateDestroyAPIView,
)

urlpatterns = [
    # Tables
    path('tables/', TableListCreateAPIView.as_view(), name='table-list-create'),
    path('tables/<uuid:pk>/', TableRetrieveUpdateDestroyAPIView.as_view(), name='table-detail'),

    # Orders
    path('orders/', OrderListCreateAPIView.as_view(), name='order-list-create'),
    path('orders/<uuid:pk>/', OrderRetrieveUpdateDestroyAPIView.as_view(), name='order-detail'),
    path('orders/<uuid:pk>/status/', OrderStatusUpdateAPIView.as_view(), name='order-status-update'),

    # Order Items (nested under orders)
    path('orders/<uuid:order_pk>/items/', OrderItemListCreateAPIView.as_view(), name='order-item-list-create'),
    path('orders/<uuid:order_pk>/items/<uuid:pk>/', OrderItemRetrieveUpdateDestroyAPIView.as_view(), name='order-item-detail'),
] 