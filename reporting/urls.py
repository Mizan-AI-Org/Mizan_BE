from django.urls import path
from . import views

urlpatterns = [
    path('attendance/', views.attendance_report, name='attendance-report'),
    path('payroll/', views.payroll_report, name='payroll-report'),
    path('dashboard-metrics/', views.dashboard_metrics, name='dashboard-metrics'),
]