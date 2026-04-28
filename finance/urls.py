from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import InvoiceViewSet
from .views_agent import (
    agent_list_invoices,
    agent_mark_invoice_paid,
    agent_record_invoice,
)

router = DefaultRouter()
router.register(r"invoices", InvoiceViewSet, basename="finance-invoice")

urlpatterns = [
    *router.urls,
    path(
        "agent/invoices/record/",
        agent_record_invoice,
        name="finance-agent-invoice-record",
    ),
    path(
        "agent/invoices/mark-paid/",
        agent_mark_invoice_paid,
        name="finance-agent-invoice-mark-paid",
    ),
    path(
        "agent/invoices/list/",
        agent_list_invoices,
        name="finance-agent-invoice-list",
    ),
]
