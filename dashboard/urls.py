from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    DailyKPIListAPIView,
    StaffCapturedOrderListCreateAPIView,
    StaffCapturedOrderRetrieveUpdateDestroyAPIView,
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
from .api.action_center import ActionCenterView
from .views import mark_shift_no_show
from .views_widget_layout import (
    AgentDashboardWidgetCreateView,
    AgentDashboardWidgetsAddView,
    DashboardCustomWidgetListView,
    DashboardWidgetOrderView,
)

router = DefaultRouter()
router.register(r'tasks', TaskManagementViewSet, basename='task-management')
router.register(r'task-categories', TaskCategoryViewSet, basename='task-category')
router.register(r'analytics', DashboardAnalyticsViewSet, basename='analytics')
router.register(r'alerts', AlertViewSet, basename='alert')

urlpatterns = [
    path('', include(router.urls)),
    path('widget-order/', DashboardWidgetOrderView.as_view(), name='dashboard-widget-order'),
    path('custom-widgets/', DashboardCustomWidgetListView.as_view(), name='dashboard-custom-widgets-list'),
    path('agent/widgets/add/', AgentDashboardWidgetsAddView.as_view(), name='dashboard-agent-widgets-add'),
    path('agent/widgets/create/', AgentDashboardWidgetCreateView.as_view(), name='dashboard-agent-widgets-create'),
    path('summary/', DashboardSummaryView.as_view(), name='dashboard-summary'),
    path('action-center/', ActionCenterView.as_view(), name='dashboard-action-center'),
    path('attendance/mark-no-show/', mark_shift_no_show, name='dashboard-mark-no-show'),
    path('kpis/', DailyKPIListAPIView.as_view(), name='daily-kpi-list'),
    path('captured-orders/', StaffCapturedOrderListCreateAPIView.as_view(), name='staff-captured-orders'),
    path(
        'captured-orders/<uuid:pk>/',
        StaffCapturedOrderRetrieveUpdateDestroyAPIView.as_view(),
        name='staff-captured-order-detail',
    ),
    path('alerts-old/', AlertListCreateAPIView.as_view(), name='alert-list-create'),
    path('alerts-old/<uuid:pk>/', AlertRetrieveUpdateDestroyAPIView.as_view(), name='alert-detail'),
    path('tasks-old/', TaskListCreateAPIView.as_view(), name='task-list-create'),
    path('tasks-old/<uuid:pk>/', TaskRetrieveUpdateDestroyAPIView.as_view(), name='task-detail'),
]
