from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ScheduleTemplateListCreateAPIView,
    ScheduleTemplateRetrieveUpdateDestroyAPIView,
    TemplateShiftListCreateAPIView,
    TemplateShiftRetrieveUpdateDestroyAPIView,
    WeeklyScheduleListCreateAPIView,
    WeeklyScheduleRetrieveUpdateDestroyAPIView,
    WeeklyScheduleViewSet,
    AssignedShiftListCreateAPIView,
    AssignedShiftRetrieveUpdateDestroyAPIView,
    AssignedShiftViewSet,
    ShiftSwapRequestListCreateAPIView,
    ShiftSwapRequestRetrieveUpdateDestroyAPIView,
    TaskCategoryViewSet,
    ShiftTaskViewSet,
)

router = DefaultRouter()
router.register(r'weekly-schedules-v2', WeeklyScheduleViewSet, basename='weekly-schedule-v2')
router.register(r'assigned-shifts-v2', AssignedShiftViewSet, basename='assigned-shift-v2')
router.register(r'task-categories', TaskCategoryViewSet, basename='task-category')
router.register(r'shift-tasks', ShiftTaskViewSet, basename='shift-task')

urlpatterns = [
    # Schedule Templates
    path('templates/', ScheduleTemplateListCreateAPIView.as_view(), name='schedule-template-list-create'),
    path('templates/<uuid:pk>/', ScheduleTemplateRetrieveUpdateDestroyAPIView.as_view(), name='schedule-template-detail'),

    # Template Shifts (nested under templates)
    path('templates/<uuid:template_pk>/shifts/', TemplateShiftListCreateAPIView.as_view(), name='template-shift-list-create'),
    path('templates/<uuid:template_pk>/shifts/<uuid:pk>/', TemplateShiftRetrieveUpdateDestroyAPIView.as_view(), name='template-shift-detail'),

    # Weekly Schedules
    path('weekly-schedules/', WeeklyScheduleListCreateAPIView.as_view(), name='weekly-schedule-list-create'),
    path('weekly-schedules/<uuid:pk>/', WeeklyScheduleRetrieveUpdateDestroyAPIView.as_view(), name='weekly-schedule-detail'),

    # Assigned Shifts (nested under weekly schedules)
    path('weekly-schedules/<uuid:schedule_pk>/assigned-shifts/', AssignedShiftListCreateAPIView.as_view(), name='assigned-shift-list-create'),
    path('weekly-schedules/<uuid:schedule_pk>/assigned-shifts/<uuid:pk>/', AssignedShiftRetrieveUpdateDestroyAPIView.as_view(), name='assigned-shift-detail'),

    # Shift Swap Requests
    path('shift-swap-requests/', ShiftSwapRequestListCreateAPIView.as_view(), name='shift-swap-request-list-create'),
    path('shift-swap-requests/<uuid:pk>/', ShiftSwapRequestRetrieveUpdateDestroyAPIView.as_view(), name='shift-swap-request-detail'),

    # Task Management
    path('', include(router.urls)),
]