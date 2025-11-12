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
    TimesheetViewSet,
    TimesheetEntryViewSet,
)
from .task_views import TaskTemplateViewSet, TaskViewSet
from .process_views import ProcessViewSet, ProcessTaskViewSet
from .template_views import ScheduleTemplateViewSet, TemplateVersionViewSet
from .audit_views import AuditLogViewSet
from .views_enhanced import CalendarAPIViewSet

router = DefaultRouter()
router.register(r'weekly-schedules-v2', WeeklyScheduleViewSet, basename='weekly-schedule-v2')
router.register(r'assigned-shifts-v2', AssignedShiftViewSet, basename='assigned-shift-v2')
router.register(r'task-categories', TaskCategoryViewSet, basename='task-category')
router.register(r'shift-tasks', ShiftTaskViewSet, basename='shift-task')
router.register(r'task-templates', TaskTemplateViewSet, basename='task-template')
router.register(r'tasks', TaskViewSet, basename='task')
router.register(r'processes', ProcessViewSet, basename='process')
router.register(r'process-tasks', ProcessTaskViewSet, basename='process-task')
router.register(r'timesheets', TimesheetViewSet, basename='timesheet')
router.register(r'timesheet-entries', TimesheetEntryViewSet, basename='timesheet-entry')
router.register(r'schedule-templates-v2', ScheduleTemplateViewSet, basename='schedule-template-v2')
router.register(r'template-versions', TemplateVersionViewSet, basename='template-version')
router.register(r'audit-logs', AuditLogViewSet, basename='audit-log')
router.register(r'calendar', CalendarAPIViewSet, basename='calendar')

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