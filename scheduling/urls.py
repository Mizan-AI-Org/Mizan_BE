from django.urls import path, include
from .views import (
    ScheduleTemplateListCreateAPIView,
    ScheduleTemplateRetrieveUpdateDestroyAPIView,
    TemplateShiftListCreateAPIView,
    TemplateShiftRetrieveUpdateDestroyAPIView,
    WeeklyScheduleListCreateAPIView,
    WeeklyScheduleRetrieveUpdateDestroyAPIView,
    AssignedShiftListCreateAPIView,
    AssignedShiftRetrieveUpdateDestroyAPIView,
    ShiftSwapRequestListCreateAPIView,
    ShiftSwapRequestRetrieveUpdateDestroyAPIView,
)

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
]