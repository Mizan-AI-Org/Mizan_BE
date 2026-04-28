"""
Miya agent endpoints for the Finance / Payables tool.

Three functions Miya needs to be useful here:

- ``record_invoice``      — create a new bill from a chat message
- ``mark_invoice_paid``   — flip an existing bill to PAID
- ``list_invoices``       — read open / due-soon / overdue / by vendor

Auth follows the standard agent-endpoint chain: Bearer agent key OR
user JWT, validated by ``_resolve_restaurant_for_agent`` so the same
contract is honoured across every Miya tool.

Inputs are deliberately forgiving (camelCase or snake_case) since
chat-driven payloads come from many places (LLM tool calls, Lua,
WhatsApp preprocessor) — we normalise here so callers don't all have
to agree on a casing convention.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import permissions, status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from accounts.models import BusinessLocation

from .models import Invoice
from .serializers import InvoiceSerializer

logger = logging.getLogger(__name__)


def _get_first(data: dict, *keys: str):
    """Return first non-empty value among the given keys."""
    for k in keys:
        if k in data and data[k] not in (None, ""):
            return data[k]
    return None


def _coerce_decimal(raw) -> Decimal | None:
    if raw in (None, ""):
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def _coerce_date(raw) -> date | None:
    if not raw:
        return None
    if isinstance(raw, date):
        return raw
    parsed = parse_date(str(raw))
    if parsed:
        return parsed
    dt = parse_datetime(str(raw))
    if dt:
        return dt.date()
    # Friendly aliases.
    norm = str(raw).strip().lower()
    today = timezone.now().date()
    if norm in ("today", "now"):
        return today
    if norm == "tomorrow":
        return today + timedelta(days=1)
    if norm == "yesterday":
        return today - timedelta(days=1)
    return None


def _resolve_location(restaurant, raw) -> BusinessLocation | None:
    if not raw:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    try:
        return BusinessLocation.objects.filter(restaurant=restaurant, id=raw).first()
    except Exception:  # noqa: BLE001 — bad uuid string falls through to name match
        pass
    return BusinessLocation.objects.filter(
        restaurant=restaurant, name__iexact=raw, is_active=True
    ).first()


# ---------------------------------------------------------------------------
# record_invoice
# ---------------------------------------------------------------------------

@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_record_invoice(request):
    """
    POST /api/finance/agent/invoices/record/

    Body (snake_case or camelCase accepted):
        vendor / vendor_name           required
        amount                         required (numeric)
        currency                       optional (default tenant or USD)
        due_date / dueDate             required (YYYY-MM-DD or 'tomorrow' etc.)
        invoice_number / invoiceNumber optional but recommended (used for dedupe)
        issue_date / issueDate         optional
        category                       optional free-text bucket
        notes                          optional
        photo_url / photoUrl           optional URL to invoice scan
        location / location_id / location_name  optional branch attribution

    Dedupe: if (restaurant, vendor_name, invoice_number) already exists
    we return that row with ``created: false`` so retries are safe.
    """
    from scheduling.views_agent import _resolve_restaurant_for_agent

    restaurant, acting_user, err = _resolve_restaurant_for_agent(request)
    if err:
        return Response({"success": False, "error": err["error"]}, status=err["status"])

    data = request.data if isinstance(getattr(request, "data", None), dict) else {}

    vendor = str(_get_first(data, "vendor_name", "vendor", "supplier") or "").strip()
    if not vendor:
        return Response(
            {"success": False, "error": "Missing vendor name", "message_for_user": "I need the vendor name."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    amount = _coerce_decimal(_get_first(data, "amount", "total", "amount_due"))
    if amount is None or amount <= 0:
        return Response(
            {
                "success": False,
                "error": "Missing or invalid amount",
                "message_for_user": "I need the invoice amount as a number.",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    due_date = _coerce_date(_get_first(data, "due_date", "dueDate", "due"))
    if due_date is None:
        return Response(
            {
                "success": False,
                "error": "Missing or invalid due_date",
                "message_for_user": "I need a due date for this invoice (e.g. 'tomorrow' or '2026-05-15').",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    issue_date = _coerce_date(_get_first(data, "issue_date", "issueDate"))
    invoice_number = str(_get_first(data, "invoice_number", "invoiceNumber", "number") or "").strip()[:120]
    currency = str(_get_first(data, "currency") or getattr(restaurant, "currency", None) or "USD").upper()[:8]
    category = str(_get_first(data, "category") or "").strip()[:50]
    notes = str(_get_first(data, "notes", "description") or "").strip()
    photo_url = str(_get_first(data, "photo_url", "photoUrl", "image_url") or "").strip()[:1024]
    location_raw = _get_first(data, "location_id", "locationId", "location", "location_name")

    # Dedupe — same vendor + same invoice number on the same tenant.
    if invoice_number:
        existing = Invoice.objects.filter(
            restaurant=restaurant,
            vendor_name__iexact=vendor,
            invoice_number__iexact=invoice_number,
        ).first()
        if existing:
            return Response(
                {
                    "success": True,
                    "created": False,
                    "invoice": InvoiceSerializer(existing).data,
                    "message_for_user": (
                        f"Already have invoice {invoice_number} from {vendor} on file "
                        f"({existing.amount} {existing.currency}, status {existing.status})."
                    ),
                },
                status=status.HTTP_200_OK,
            )

    location = _resolve_location(restaurant, location_raw)

    invoice = Invoice.objects.create(
        restaurant=restaurant,
        location=location,
        vendor_name=vendor[:200],
        invoice_number=invoice_number,
        amount=amount,
        currency=currency,
        issue_date=issue_date,
        due_date=due_date,
        status=Invoice.STATUS_OPEN,
        category=category,
        notes=notes,
        photo_url=photo_url,
        created_by=acting_user,
    )

    days_left = invoice.days_until_due
    if days_left is None:
        when = due_date.isoformat()
    elif days_left == 0:
        when = "today"
    elif days_left == 1:
        when = "tomorrow"
    elif days_left < 0:
        when = f"{abs(days_left)} day{'s' if abs(days_left) != 1 else ''} ago"
    else:
        when = f"in {days_left} day{'s' if days_left != 1 else ''}"
    msg = (
        f"Logged {currency} {amount} invoice from {vendor}"
        + (f" (#{invoice_number})" if invoice_number else "")
        + f", due {when}."
    )

    return Response(
        {
            "success": True,
            "created": True,
            "invoice": InvoiceSerializer(invoice).data,
            "message_for_user": msg,
        },
        status=status.HTTP_201_CREATED,
    )


# ---------------------------------------------------------------------------
# mark_invoice_paid
# ---------------------------------------------------------------------------

@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_mark_invoice_paid(request):
    """
    POST /api/finance/agent/invoices/mark-paid/

    Body:
        invoice_id            preferred — exact UUID
        OR (vendor + invoice_number)  fuzzy match for chat usage
        paid_on               optional, default now
        method                optional CASH/CARD/BANK_TRANSFER/CHEQUE/...
        reference             optional cheque/transfer ref
        amount                optional (defaults to invoice.amount)
    """
    from scheduling.views_agent import _resolve_restaurant_for_agent

    restaurant, acting_user, err = _resolve_restaurant_for_agent(request)
    if err:
        return Response({"success": False, "error": err["error"]}, status=err["status"])

    data = request.data if isinstance(getattr(request, "data", None), dict) else {}
    invoice_id = _get_first(data, "invoice_id", "invoiceId", "id")
    invoice = None
    if invoice_id:
        invoice = Invoice.objects.filter(restaurant=restaurant, id=invoice_id).first()
    if invoice is None:
        vendor = str(_get_first(data, "vendor", "vendor_name", "supplier") or "").strip()
        invoice_number = str(_get_first(data, "invoice_number", "invoiceNumber", "number") or "").strip()
        if vendor or invoice_number:
            qs = Invoice.objects.filter(restaurant=restaurant)
            if vendor:
                qs = qs.filter(vendor_name__icontains=vendor)
            if invoice_number:
                qs = qs.filter(invoice_number__iexact=invoice_number)
            qs = qs.exclude(status=Invoice.STATUS_VOIDED).order_by("-due_date")
            invoice = qs.first()

    if invoice is None:
        return Response(
            {
                "success": False,
                "error": "Invoice not found",
                "message_for_user": (
                    "I couldn't find that invoice. Tell me the invoice id, or "
                    "the vendor + invoice number."
                ),
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    if invoice.status == Invoice.STATUS_PAID:
        return Response(
            {
                "success": True,
                "already_paid": True,
                "invoice": InvoiceSerializer(invoice).data,
                "message_for_user": (
                    f"That {invoice.vendor_name} invoice is already marked paid"
                    + (f" on {invoice.paid_at.date().isoformat()}." if invoice.paid_at else ".")
                ),
            },
            status=status.HTTP_200_OK,
        )

    paid_on = _coerce_date(_get_first(data, "paid_on", "paidOn", "paid_at"))
    method = str(_get_first(data, "method", "payment_method") or "").upper()
    reference = str(_get_first(data, "reference", "payment_reference") or "")
    amount = _coerce_decimal(_get_first(data, "amount"))

    invoice.mark_paid(
        paid_on=paid_on,
        method=method,
        reference=reference,
        amount=amount,
        user=acting_user,
    )
    msg = (
        f"Marked {invoice.vendor_name} invoice "
        f"({invoice.amount} {invoice.currency}) as paid"
        + (f" via {method.lower().replace('_', ' ')}" if method else "")
        + "."
    )
    return Response(
        {"success": True, "invoice": InvoiceSerializer(invoice).data, "message_for_user": msg},
        status=status.HTTP_200_OK,
    )


# ---------------------------------------------------------------------------
# list_invoices
# ---------------------------------------------------------------------------

@api_view(["GET", "POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_list_invoices(request):
    """
    GET/POST /api/finance/agent/invoices/list/

    Query params (or body):
        status         OPEN|PAID|VOIDED|DRAFT|ALL  (default OPEN)
        vendor         partial match
        overdue        bool — open + due_date < today
        due_within     int days — open + due in [today, today+N]
        limit          default 25, max 100
    """
    from scheduling.views_agent import _resolve_restaurant_for_agent

    restaurant, _, err = _resolve_restaurant_for_agent(request)
    if err:
        return Response({"success": False, "error": err["error"]}, status=err["status"])

    src = request.query_params if request.method == "GET" else (
        request.data if isinstance(getattr(request, "data", None), dict) else {}
    )
    st = str(src.get("status") or "OPEN").upper()
    qs = Invoice.objects.filter(restaurant=restaurant).select_related("location")

    if st != "ALL":
        if st in {Invoice.STATUS_OPEN, Invoice.STATUS_PAID, Invoice.STATUS_VOIDED, Invoice.STATUS_DRAFT}:
            qs = qs.filter(status=st)

    vendor = str(src.get("vendor") or "").strip()
    if vendor:
        qs = qs.filter(vendor_name__icontains=vendor)

    if str(src.get("overdue") or "").lower() in ("1", "true", "yes"):
        qs = qs.filter(status=Invoice.STATUS_OPEN, due_date__lt=timezone.now().date())

    due_within = src.get("due_within")
    if due_within not in (None, ""):
        try:
            n = int(due_within)
            today = timezone.now().date()
            qs = qs.filter(
                status=Invoice.STATUS_OPEN,
                due_date__gte=today,
                due_date__lte=today + timedelta(days=n),
            )
        except (TypeError, ValueError):
            pass

    try:
        limit = max(1, min(int(src.get("limit") or 25), 100))
    except (TypeError, ValueError):
        limit = 25

    rows = list(qs.order_by("due_date", "-created_at")[:limit])
    today = timezone.now().date()
    overdue_count = sum(1 for r in rows if r.status == Invoice.STATUS_OPEN and r.due_date and r.due_date < today)

    if not rows:
        message = "No invoices match those filters."
    else:
        bits = [f"{len(rows)} invoice{'s' if len(rows) != 1 else ''}"]
        if overdue_count:
            bits.append(f"{overdue_count} overdue")
        message = ", ".join(bits) + ":\n" + "\n".join(
            (
                f"• {r.vendor_name}"
                + (f" #{r.invoice_number}" if r.invoice_number else "")
                + f" — {r.currency} {r.amount}, due {r.due_date.isoformat() if r.due_date else 'unscheduled'}"
                + (" (OVERDUE)" if r.status == Invoice.STATUS_OPEN and r.due_date and r.due_date < today else "")
            )
            for r in rows[:10]
        )

    return Response(
        {
            "success": True,
            "count": len(rows),
            "overdue_count": overdue_count,
            "invoices": InvoiceSerializer(rows, many=True).data,
            "message_for_user": message,
        },
        status=status.HTTP_200_OK,
    )
