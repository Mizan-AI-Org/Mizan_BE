"""JWT APIs for PayGuard policy + invoice payment approvals."""
from __future__ import annotations

from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from finance.models import Invoice, InvoicePaymentApproval
from finance.payment_approval import (
    act_on_approval,
    get_policy,
    save_policy,
    serialize_approval,
    serialize_policy_for_ui,
    start_payment_approval,
)


def _manager_ok(user) -> bool:
    role = (getattr(user, "role", "") or "").upper()
    return role in {"SUPER_ADMIN", "ADMIN", "OWNER", "MANAGER"}


@api_view(["GET", "PUT", "PATCH"])
@permission_classes([permissions.IsAuthenticated])
def payment_approval_policy(request):
    """Get or update PayGuard ladder rules (Settings)."""
    restaurant = getattr(request.user, "restaurant", None)
    if not restaurant:
        return Response({"success": False, "error": "No restaurant"}, status=400)
    if not _manager_ok(request.user):
        return Response({"success": False, "error": "Permission denied"}, status=403)

    if request.method == "GET":
        return Response(
            {
                "success": True,
                "policy": serialize_policy_for_ui(get_policy(restaurant)),
            }
        )

    data = request.data if isinstance(request.data, dict) else {}
    policy_in = data.get("policy") if isinstance(data.get("policy"), dict) else data
    saved = save_policy(restaurant, policy_in)
    return Response(
        {
            "success": True,
            "policy": saved,
            "message": "PayGuard ladder saved. New invoices will use these rungs.",
        }
    )


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def payment_approvals_pending(request):
    """List pending PayGuard runs for the restaurant."""
    restaurant = getattr(request.user, "restaurant", None)
    if not restaurant:
        return Response({"success": False, "error": "No restaurant"}, status=400)
    qs = (
        InvoicePaymentApproval.objects.filter(
            restaurant=restaurant, status=InvoicePaymentApproval.STATUS_PENDING
        )
        .select_related("invoice", "requested_by")
        .prefetch_related("steps")
        .order_by("started_at")[:40]
    )
    rows = []
    for a in qs:
        inv = a.invoice
        payload = serialize_approval(a)
        payload["invoice"] = {
            "id": str(inv.id),
            "vendor_name": inv.vendor_name,
            "amount": str(inv.amount),
            "currency": inv.currency,
            "invoice_number": inv.invoice_number,
            "due_date": inv.due_date.isoformat() if inv.due_date else None,
        }
        payload["requested_by_name"] = (
            f"{a.requested_by.first_name} {a.requested_by.last_name}".strip()
            if a.requested_by
            else None
        )
        rows.append(payload)
    return Response({"success": True, "count": len(rows), "approvals": rows})


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def payment_approval_start(request, invoice_id=None):
    restaurant = getattr(request.user, "restaurant", None)
    if not restaurant or not _manager_ok(request.user):
        return Response({"success": False, "error": "Permission denied"}, status=403)
    inv_id = invoice_id or request.data.get("invoice_id")
    invoice = Invoice.objects.filter(id=inv_id, restaurant=restaurant).first()
    if not invoice:
        return Response({"success": False, "error": "Invoice not found"}, status=404)
    result = start_payment_approval(invoice=invoice, requested_by=request.user)
    code = status.HTTP_200_OK if result.get("success") else status.HTTP_400_BAD_REQUEST
    return Response(result, status=code)


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def payment_approval_act(request, invoice_id=None):
    restaurant = getattr(request.user, "restaurant", None)
    if not restaurant:
        return Response({"success": False, "error": "No restaurant"}, status=400)
    inv_id = invoice_id or request.data.get("invoice_id")
    invoice = Invoice.objects.filter(id=inv_id, restaurant=restaurant).first()
    if not invoice:
        return Response({"success": False, "error": "Invoice not found"}, status=404)
    action = str(request.data.get("action") or "").lower()
    note = str(request.data.get("note") or "")
    result = act_on_approval(invoice=invoice, actor=request.user, action=action, note=note)
    code = status.HTTP_200_OK if result.get("success") else status.HTTP_400_BAD_REQUEST
    return Response(result, status=code)
