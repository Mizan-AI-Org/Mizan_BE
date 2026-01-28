from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    DailyKPIListAPIView,
    AlertListCreateAPIView,
    AlertRetrieveUpdateDestroyAPIView,
    TaskListCreateAPIView,
    TaskRetrieveUpdateDestroyAPIView,
)
from .views_extended import (
    TaskManagementViewSet,
    TaskCategoryViewSet,
    DashboardAnalyticsViewSet,
    AlertViewSet
)
from .api.summary import DashboardSummaryView

router = DefaultRouter()
router.register(r'tasks', TaskManagementViewSet, basename='task-management')
router.register(r'task-categories', TaskCategoryViewSet, basename='task-category')
router.register(r'analytics', DashboardAnalyticsViewSet, basename='analytics')
router.register(r'alerts', AlertViewSet, basename='alert')

urlpatterns = [
    path('', include(router.urls)),
    path('summary/', DashboardSummaryView.as_view(), name='dashboard-summary'),
    path('kpis/', DailyKPIListAPIView.as_view(), name='daily-kpi-list'),
    path('alerts-old/', AlertListCreateAPIView.as_view(), name='alert-list-create'),
    path('alerts-old/<uuid:pk>/', AlertRetrieveUpdateDestroyAPIView.as_view(), name='alert-detail'),
    path('tasks-old/', TaskListCreateAPIView.as_view(), name='task-list-create'),
    path('tasks-old/<uuid:pk>/', TaskRetrieveUpdateDestroyAPIView.as_view(), name='task-detail'),
]
