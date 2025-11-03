"""
URL configuration for the checklists app
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

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
]