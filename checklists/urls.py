"""
URL configuration for the checklists app
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views
from .views_agent import (
    agent_list_checklists_for_review,
    agent_approve_checklist,
    agent_reject_checklist,
)

# Create a router and register our viewsets with it
router = DefaultRouter()
router.register(r'templates', views.ChecklistTemplateViewSet, basename='checklist-template')
router.register(r'executions', views.ChecklistExecutionViewSet, basename='checklist-execution')
router.register(r'step-responses', views.ChecklistStepResponseViewSet, basename='checklist-step-response')
router.register(r'actions', views.ChecklistActionViewSet, basename='checklist-action')
router.register(r'analytics', views.ChecklistAnalyticsViewSet, basename='checklist-analytics')

# The API URLs are now determined automatically by the router
urlpatterns = [
    path('api/checklists/', include(router.urls)),
    path('api/checklists/shift-checklists/', views.get_shift_checklists, name='get-shift-checklists'),
    path('api/checklists/agent/shift-checklists/', views.agent_get_shift_checklists, name='agent-get-shift-checklists'),
    path('api/checklists/agent/initiate/', views.agent_initiate_shift_checklists, name='agent-initiate-shift-checklists'),
    path('api/checklists/agent/executions/<uuid:execution_id>/sync/', views.agent_sync_checklist_response, name='agent-sync-checklist-response'),
    path('api/checklists/agent/executions/<uuid:execution_id>/complete/', views.agent_complete_checklist_execution, name='agent-complete-checklist-execution'),
    path('api/checklists/agent/review/list/', agent_list_checklists_for_review, name='agent-list-checklists-for-review'),
    path('api/checklists/agent/review/approve/', agent_approve_checklist, name='agent-approve-checklist'),
    path('api/checklists/agent/review/reject/', agent_reject_checklist, name='agent-reject-checklist'),
]