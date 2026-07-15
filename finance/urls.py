from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import InvoiceViewSet
from .views_agent import (
    agent_confirm_invoice_po_match,
    agent_list_invoices,
    agent_mark_invoice_paid,
    agent_match_invoice_po,
    agent_payment_approval,
    agent_record_invoice,
    agent_update_invoice_bank_payment_status,
)
from .views_payment_approval import (
    payment_approval_act,
    payment_approval_policy,
    payment_approval_start,
    payment_approvals_pending,
)

router = DefaultRouter()
router.register(r"invoices", InvoiceViewSet, basename="finance-invoice")

urlpatterns = [
    *router.urls,
    path(
        "payment-approval/policy/",
        payment_approval_policy,
        name="finance-payment-approval-policy",
    ),
    path(
        "payment-approval/pending/",
        payment_approvals_pending,
        name="finance-payment-approval-pending",
    ),
    path(
        "payment-approval/start/",
        payment_approval_start,
        name="finance-payment-approval-start",
    ),
    path(
        "payment-approval/act/",
        payment_approval_act,
        name="finance-payment-approval-act",
    ),
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
    path(
        "agent/payment-approval/",
        agent_payment_approval,
        name="finance-agent-payment-approval",
    ),
]
