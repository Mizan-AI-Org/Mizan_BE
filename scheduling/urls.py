from django.urls import path
from .views import (
    ScheduleTemplateListCreateView, ScheduleTemplateDetailView,
    WeeklyScheduleGenerateView, WeeklyScheduleDetailView,
    ShiftDetailAPIView, MyAssignedShiftsView, weekly_schedule_view,
    ShiftSwapRequestCreateView, MyShiftSwapRequestsView, ManagerShiftSwapRequestsView, ShiftSwapRequestActionView
)

urlpatterns = [
    path('templates/', ScheduleTemplateListCreateView.as_view(), name='schedule-template-list-create'),
    path('templates/<uuid:pk>/', ScheduleTemplateDetailView.as_view(), name='schedule-template-detail'),
    path('generate-weekly-schedule/', WeeklyScheduleGenerateView.as_view(), name='generate-weekly-schedule'),
    path('weekly-schedules/<uuid:pk>/', WeeklyScheduleDetailView.as_view(), name='weekly-schedule-detail'),
    path('weekly-schedule/', weekly_schedule_view, name='weekly-schedule-view'),
    path('assigned-shifts/<uuid:pk>/', ShiftDetailAPIView.as_view(), name='assigned-shift-detail'),
    path('my-shifts/', MyAssignedShiftsView.as_view(), name='my-assigned-shifts'),
    path('shift-swap-requests/', ShiftSwapRequestCreateView.as_view(), name='shift-swap-request-create'),
    path('my-shift-swap-requests/', MyShiftSwapRequestsView.as_view(), name='my-shift-swap-requests'),
    path('manager-shift-swap-requests/', ManagerShiftSwapRequestsView.as_view(), name='manager-shift-swap-requests'),
    path('shift-swap-requests/<uuid:pk>/<str:action>/', ShiftSwapRequestActionView.as_view(), name='shift-swap-request-action'),
]