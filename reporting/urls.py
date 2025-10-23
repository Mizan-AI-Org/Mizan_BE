from django.urls import path
from .views import (
    DailySalesReportListAPIView,
    DailySalesReportRetrieveAPIView,
    AttendanceReportListAPIView,
    AttendanceReportRetrieveAPIView,
    InventoryReportListAPIView,
    InventoryReportRetrieveAPIView,
)

urlpatterns = [
    # Daily Sales Reports
    path('sales/daily/', DailySalesReportListAPIView.as_view(), name='daily-sales-report-list'),
    path('sales/daily/<uuid:pk>/', DailySalesReportRetrieveAPIView.as_view(), name='daily-sales-report-detail'),

    # Attendance Reports
    path('attendance/', AttendanceReportListAPIView.as_view(), name='attendance-report-list'),
    path('attendance/<uuid:pk>/', AttendanceReportRetrieveAPIView.as_view(), name='attendance-report-detail'),

    # Inventory Reports
    path('inventory/', InventoryReportListAPIView.as_view(), name='inventory-report-list'),
    path('inventory/<uuid:pk>/', InventoryReportRetrieveAPIView.as_view(), name='inventory-report-detail'),
]