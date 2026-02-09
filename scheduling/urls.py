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
from .schedule_photo_views import parse_schedule_photo, parse_schedule_document, apply_parsed_schedule
from .views_agent import (
    agent_list_staff,
    agent_staff_count,
    agent_list_task_templates,
    agent_create_task_template,
    agent_attach_templates_to_shift,
    agent_list_shifts,
    agent_create_shift,
    agent_send_shift_notification,
    agent_optimize_schedule,
    agent_restaurant_search,
    agent_get_restaurant_details,
    agent_get_operational_advice,
    agent_staff_by_phone,
    agent_get_my_shifts,
    agent_detect_conflicts,
    agent_memory_list_or_save,
    agent_memory_delete,
    agent_proactive_insights,
)

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
    path('auto-schedule/', WeeklyScheduleViewSet.as_view({'post': 'optimize'}), name='auto-schedule'),

    # Assigned Shifts (nested under weekly schedules)
    path('weekly-schedules/<uuid:schedule_pk>/assigned-shifts/', AssignedShiftListCreateAPIView.as_view(), name='assigned-shift-list-create'),
    path('weekly-schedules/<uuid:schedule_pk>/assigned-shifts/<uuid:pk>/', AssignedShiftRetrieveUpdateDestroyAPIView.as_view(), name='assigned-shift-detail'),

    # Shift Swap Requests
    path('shift-swap-requests/', ShiftSwapRequestListCreateAPIView.as_view(), name='shift-swap-request-list-create'),
    path('shift-swap-requests/<uuid:pk>/', ShiftSwapRequestRetrieveUpdateDestroyAPIView.as_view(), name='shift-swap-request-detail'),

    # Schedule photo import (7Shifts-style: upload/snap → parse → template + apply)
    path('parse-schedule-photo/', parse_schedule_photo, name='parse-schedule-photo'),
    path('parse-schedule-document/', parse_schedule_document, name='parse-schedule-document'),
    path('apply-parsed-schedule/', apply_parsed_schedule, name='apply-parsed-schedule'),

    # Task Management
    path('', include(router.urls)),
    
    # Agent Integration (authenticated via LUA_WEBHOOK_API_KEY)
    path('agent/staff/', agent_list_staff, name='agent_list_staff'),
    path('agent/staff-count/', agent_staff_count, name='agent_staff_count'),
    path('agent/task-templates/', agent_list_task_templates, name='agent_list_task_templates'),
    path('agent/create-task-template/', agent_create_task_template, name='agent_create_task_template'),
    path('agent/attach-templates-to-shift/', agent_attach_templates_to_shift, name='agent_attach_templates_to_shift'),
    path('agent/list-shifts/', agent_list_shifts, name='agent_list_shifts'),
    path('agent/create-shift/', agent_create_shift, name='agent_create_shift'),
    path('agent/notify-shift/', agent_send_shift_notification, name='agent_notify_shift'),
    path('agent/optimize-schedule/', agent_optimize_schedule, name='agent_optimize_schedule'),
    path('agent/restaurant-search/', agent_restaurant_search, name='agent_restaurant_search'),
    path('agent/restaurant-details/', agent_get_restaurant_details, name='agent_get_restaurant_details'),
    path('agent/operational-advice/', agent_get_operational_advice, name='agent_get_operational_advice'),
    path('agent/staff-by-phone/', agent_staff_by_phone, name='agent_staff_by_phone'),
    path('agent/my-shifts/', agent_get_my_shifts, name='agent_get_my_shifts'),
    path('agent/detect-conflicts/', agent_detect_conflicts, name='agent_detect_conflicts'),
    path('agent/memories/', agent_memory_list_or_save, name='agent_memory_list_or_save'),
    path('agent/memories/delete/', agent_memory_delete, name='agent_memory_delete'),
    path('agent/proactive-insights/', agent_proactive_insights, name='agent_proactive_insights'),
]