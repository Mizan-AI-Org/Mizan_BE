from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import InvoiceViewSet
from .views_agent import (
    agent_confirm_invoice_po_match,
    agent_list_invoices,
    agent_mark_invoice_paid,
    agent_match_invoice_po,
    agent_record_invoice,
    agent_update_invoice_bank_payment_status,
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
    path(
        "agent/invoices/payment-status/",
        agent_update_invoice_bank_payment_status,
        name="finance-agent-invoice-payment-status",
    ),
    path(
        "agent/invoices/match-po/",
        agent_match_invoice_po,
        name="finance-agent-invoice-match-po",
    ),
    path(
        "agent/invoices/confirm-po-match/",
        agent_confirm_invoice_po_match,
        name="finance-agent-invoice-confirm-po-match",
    ),
]
