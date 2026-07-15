from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views_agent
from .views_compliance_docs import ComplianceDocumentViewSet

router = DefaultRouter()
router.register(
    r"compliance-documents",
    ComplianceDocumentViewSet,
    basename="compliance-document",
)

urlpatterns = [
    path(
        "agent/payslips/generate/",
        views_agent.agent_generate_payslips,
        name="payroll-agent-generate-payslips",
    ),
    path(
        "agent/compliance-reminders/list/",
        views_agent.agent_compliance_reminders,
        name="payroll-agent-compliance-list",
    ),
    path(
        "agent/compliance-reminders/seed/",
        views_agent.agent_compliance_reminders,
        name="payroll-agent-compliance-seed",
    ),
    path(
        "agent/compliance-documents/",
        views_agent.agent_compliance_documents,
        name="payroll-agent-compliance-documents",
    ),
    path(
        "agent/compliance-documents/seed/",
        views_agent.agent_compliance_documents,
        name="payroll-agent-compliance-documents-seed",
    ),
    path(
        "agent/temperature-log/",
        views_agent.agent_log_temperature,
        name="payroll-agent-temperature-log",
    ),
    path(
        "agent/delivery-menu/sync/",
        views_agent.agent_sync_delivery_menu,
        name="payroll-agent-delivery-menu-sync",
    ),
    path("", include(router.urls)),
]
