from django.urls import path
from .views import (
    DailySalesReportListAPIView,
    DailySalesReportRetrieveAPIView,
    AttendanceReportListAPIView,
    AttendanceReportRetrieveAPIView,
    InventoryReportListAPIView,
    InventoryReportRetrieveAPIView,
    IncidentListAPIView,
    IncidentCreateAPIView,
    labor_planned_vs_actual,
    labor_compliance,
    labor_certifications_expiring,
    labor_sales_recommendation,
    LaborBudgetListCreateAPIView,
    LaborPolicyAPIView,
)
from .views_export import attendance_export, agent_attendance_export
from .views_agent import agent_create_incident

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

    # Incidents
    path('incidents/', IncidentListAPIView.as_view(), name='incident-list'),
    path('incidents/create/', IncidentCreateAPIView.as_view(), name='incident-create'),
    
    # Agent-Authenticated Incidents
    path('agent/create-incident/', agent_create_incident, name='agent_create_incident'),

    # Labor: planned vs actual, compliance, certifications, sales recommendation, budget, policy
    path('labor/planned-vs-actual/', labor_planned_vs_actual, name='labor_planned_vs_actual'),
    path('labor/compliance/', labor_compliance, name='labor_compliance'),
    path('labor/certifications-expiring/', labor_certifications_expiring, name='labor_certifications_expiring'),
    path('labor/sales-recommendation/', labor_sales_recommendation, name='labor_sales_recommendation'),
    path('labor/budgets/', LaborBudgetListCreateAPIView.as_view(), name='labor_budget_list_create'),
    path('labor/policy/', LaborPolicyAPIView.as_view(), name='labor_policy'),

    # Staff Attendance Report export for HR / payroll (PDF, Excel)
    path('attendance/export/', attendance_export, name='attendance_export'),
    path('agent/attendance-export/', agent_attendance_export, name='agent_attendance_export'),
]