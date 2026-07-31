from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r"", views.TenantAutomationViewSet, basename="tenant-automation")

urlpatterns = [
    path("agent/create/", views.agent_create_automation, name="automations-agent-create"),
    path("agent/list/", views.agent_list_automations, name="automations-agent-list"),
    path("", include(router.urls)),
]
