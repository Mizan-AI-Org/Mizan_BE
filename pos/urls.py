from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    TableViewSet, OrderViewSet, OrderLineItemViewSet,
    PaymentViewSet, POSTransactionViewSet, ReceiptSettingViewSet,
    DiscountViewSet
)

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
]