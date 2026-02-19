from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ScheduleViewSet, StaffProfileViewSet, ScheduleChangeViewSet,
    ScheduleNotificationViewSet, StaffAvailabilityViewSet, PerformanceMetricViewSet,
    StandardOperatingProcedureViewSet, SafetyChecklistViewSet, ScheduleTaskViewSet,
    SafetyConcernReportViewSet, SafetyRecognitionViewSet,
    update_staff_profile_by_user_id,
    StaffDocumentViewSet,
    StaffRequestViewSet,
)
from .views_agent import (
    agent_ingest_staff_request,
    agent_list_staff_requests,
    agent_approve_staff_request,
    agent_reject_staff_request,
    agent_list_incidents,
    agent_close_incident,
    agent_escalate_incident,
)

router = DefaultRouter()
router.register(r'schedules', ScheduleViewSet)
router.register(r'profiles', StaffProfileViewSet)
router.register(r'schedule-changes', ScheduleChangeViewSet)
router.register(r'notifications', ScheduleNotificationViewSet)
router.register(r'availability', StaffAvailabilityViewSet)
router.register(r'performance', PerformanceMetricViewSet)
router.register(r'sops', StandardOperatingProcedureViewSet, basename='sop')
router.register(r'safety-checklists', SafetyChecklistViewSet, basename='safety-checklist')
router.register(r'schedule-tasks', ScheduleTaskViewSet, basename='schedule-task')
router.register(r'safety-concerns', SafetyConcernReportViewSet, basename='safety-concern')
router.register(r'safety-recognitions', SafetyRecognitionViewSet, basename='safety-recognition')
router.register(r'documents', StaffDocumentViewSet, basename='document')
router.register(r'requests', StaffRequestViewSet, basename='staff-request')

urlpatterns = [
    path('', include(router.urls)),
    path('profile/<str:staff_id>/update/', update_staff_profile_by_user_id, name='update-staff-profile-by-user'),
    path('agent/requests/ingest/', agent_ingest_staff_request, name='agent-ingest-staff-request'),
    path('agent/requests/', agent_list_staff_requests, name='agent-list-staff-requests'),
    path('agent/requests/approve/', agent_approve_staff_request, name='agent-approve-staff-request'),
    path('agent/requests/reject/', agent_reject_staff_request, name='agent-reject-staff-request'),
    path('agent/incidents/', agent_list_incidents, name='agent-list-incidents'),
    path('agent/incidents/close/', agent_close_incident, name='agent-close-incident'),
    path('agent/incidents/escalate/', agent_escalate_incident, name='agent-escalate-incident'),
]