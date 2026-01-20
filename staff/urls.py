from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ScheduleViewSet, StaffProfileViewSet, ScheduleChangeViewSet,
    ScheduleNotificationViewSet, StaffAvailabilityViewSet, PerformanceMetricViewSet,
    StandardOperatingProcedureViewSet, SafetyChecklistViewSet, ScheduleTaskViewSet,
    SafetyConcernReportViewSet, SafetyRecognitionViewSet,
    update_staff_profile_by_user_id,
    StaffDocumentViewSet
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

urlpatterns = [
    path('', include(router.urls)),
    path('profile/<str:staff_id>/update/', update_staff_profile_by_user_id, name='update-staff-profile-by-user'),
]