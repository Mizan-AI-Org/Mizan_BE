from django.urls import path
from .views import (
    DailyKPIListAPIView,
    AlertListCreateAPIView,
    AlertRetrieveUpdateDestroyAPIView,
    TaskListCreateAPIView,
    TaskRetrieveUpdateDestroyAPIView,
)

urlpatterns = [
    path('kpis/', DailyKPIListAPIView.as_view(), name='daily-kpi-list'),
    path('alerts/', AlertListCreateAPIView.as_view(), name='alert-list-create'),
    path('alerts/<uuid:pk>/', AlertRetrieveUpdateDestroyAPIView.as_view(), name='alert-detail'),
    path('tasks/', TaskListCreateAPIView.as_view(), name='task-list-create'),
    path('tasks/<uuid:pk>/', TaskRetrieveUpdateDestroyAPIView.as_view(), name='task-detail'),
]
