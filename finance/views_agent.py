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
from core.read_through_cache import get_or_set, safe_cache_delete

from dashboard.api.agent_dates import coerce_agent_date
from .models import Invoice
from .serializers import InvoiceSerializer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Read-through cache for agent_list_invoices.
#
# Miya hits this every time a manager asks "any unpaid bills?" /
# "show me what's overdue" — often several times per conversation as
# she sanity-checks state before/after a record_invoice or
# mark_invoice_paid call. A short (45s) cache keyed by the full filter
# tuple (restaurant, status, vendor, overdue, due_within, limit) gives
# us near-zero RDS traffic for repeated reads inside a single turn
# without making the feed meaningfully stale. Writes invalidate every
# slice for the tenant via a post_save signal (finance/signals.py).
# ---------------------------------------------------------------------------

_INVOICES_CACHE_TTL = 45
_INVOICES_CACHE_NS = "agent:finance:invoices:v1"


def _invoices_cache_key(restaurant_id, filters: tuple) -> str:
    # filters is a small tuple of already-normalised primitives; hashing
    # it keeps the key short and collision-safe across Python sessions.
    import hashlib

    payload = "|".join(str(x) for x in filters)
    h = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:32]
    return f"{_INVOICES_CACHE_NS}:{restaurant_id}:{h}"


def _invoices_cache_index_key(restaurant_id) -> str:
    """Set of cache keys outstanding for this tenant. Used by the
    invalidator so we can wipe every filter slice with one Redis call
    (vs. iterating all possible combos).
    """
    return f"{_INVOICES_CACHE_NS}:idx:{restaurant_id}"


def _remember_invoices_cache_key(restaurant_id, key: str) -> None:
    """Best-effort: track which slices exist for this tenant so the
    invalidator can bust them all on any write. Uses Django's cache
    API (SET semantics via a dict-of-None-values so eviction works
    cleanly on both Redis and local-memory backends).
    """
    from django.core.cache import cache

    try:
        current = cache.get(_invoices_cache_index_key(restaurant_id)) or {}
        if not isinstance(current, dict):
            current = {}
        if key not in current:
            current[key] = 1
            # Index TTL slightly longer than slice TTL so the index
            # doesn't expire before the slices it points to.
            cache.set(
                _invoices_cache_index_key(restaurant_id),
                current,
                _INVOICES_CACHE_TTL * 4,
            )
    except Exception:
        # Cache is an optimisation — never propagate failures.
        pass


def invalidate_invoices_cache(restaurant_id) -> None:
    """Wipe every cached slice of agent_list_invoices for this tenant.
    Exposed as module-level so the post_save signal (and any future
    admin save hook) can call it without importing private helpers.
    """
    from django.core.cache import cache

    idx_key = _invoices_cache_index_key(restaurant_id)
    try:
        current = cache.get(idx_key) or {}
    except Exception:
        current = {}
    if isinstance(current, dict):
        for k in list(current.keys()):
            safe_cache_delete(k)
    safe_cache_delete(idx_key)


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
    return coerce_agent_date(raw)


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
            if photo_url and not (existing.attachment or existing.photo):
                from finance.attachment_utils import attach_invoice_from_url

                attach_invoice_from_url(existing, photo_url)
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

    if photo_url:
        from finance.attachment_utils import attach_invoice_from_url

        attach_invoice_from_url(invoice, photo_url)

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

    payguard = None
    try:
        from finance.payment_approval import get_policy, start_payment_approval

        if get_policy(restaurant).get("enabled"):
            payguard = start_payment_approval(invoice=invoice, requested_by=acting_user)
            if payguard.get("approval_required"):
                msg += " " + (payguard.get("message_for_user") or "PayGuard approval started.")
    except Exception:
        logger.exception("PayGuard auto-start failed for invoice %s", invoice.id)

    return Response(
        {
            "success": True,
            "created": True,
            "invoice": InvoiceSerializer(invoice).data,
            "payguard": payguard,
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

    from finance.payment_approval import payment_allowed, start_payment_approval

    # If PayGuard is on and approval never started, kick it off instead of paying
    ok, block_msg = payment_allowed(invoice)
    if not ok:
        if invoice.approval_status == Invoice.APPROVAL_NONE:
            started = start_payment_approval(invoice=invoice, requested_by=acting_user)
            return Response(
                {
                    "success": False,
                    "error": "approval_required",
                    "payguard": started,
                    "message_for_user": (
                        started.get("message_for_user")
                        or "PayGuard needs approval before this can be paid."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {
                "success": False,
                "error": "approval_required",
                "message_for_user": block_msg,
            },
            status=status.HTTP_400_BAD_REQUEST,
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
    invoice.bank_payment_status = Invoice.BANK_PAYMENT_CLEARED
    invoice.save(update_fields=["bank_payment_status", "updated_at"])
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
        msg = err["error"]
        return Response(
            {"success": False, "error": msg, "message_for_user": msg},
            status=err["status"],
        )

    src = request.query_params if request.method == "GET" else (
        request.data if isinstance(getattr(request, "data", None), dict) else {}
    )
    st = str(src.get("status") or "OPEN").upper()
    vendor = str(src.get("vendor") or "").strip()
    overdue_flag = str(src.get("overdue") or "").lower() in ("1", "true", "yes")

    due_within_raw = src.get("due_within")
    due_within_n: int | None
    if due_within_raw in (None, ""):
        due_within_n = None
    else:
        try:
            due_within_n = int(due_within_raw)
        except (TypeError, ValueError):
            due_within_n = None

    try:
        limit = max(1, min(int(src.get("limit") or 25), 100))
    except (TypeError, ValueError):
        limit = 25

    # Key by today's date so "overdue" / "due within N days" slices stay
    # correct across UTC rollover — otherwise a result cached at 23:59
    # would stay visible at 00:05 with yesterday's definition of "today".
    today = timezone.now().date()
    cache_filters = (st, vendor.lower(), int(overdue_flag), due_within_n, limit, today.isoformat())
    cache_key = _invoices_cache_key(restaurant.id, cache_filters)

    def _compute_invoices_payload():
        qs = Invoice.objects.filter(restaurant=restaurant).select_related("location")

        if st != "ALL":
            if st in {
                Invoice.STATUS_OPEN,
                Invoice.STATUS_PAID,
                Invoice.STATUS_VOIDED,
                Invoice.STATUS_DRAFT,
            }:
                qs = qs.filter(status=st)

        if vendor:
            qs = qs.filter(vendor_name__icontains=vendor)

        if overdue_flag:
            qs = qs.filter(status=Invoice.STATUS_OPEN, due_date__lt=today)

        if due_within_n is not None:
            qs = qs.filter(
                status=Invoice.STATUS_OPEN,
                due_date__gte=today,
                due_date__lte=today + timedelta(days=due_within_n),
            )

        rows = list(qs.order_by("due_date", "-created_at")[:limit])
        overdue_count = sum(
            1 for r in rows if r.status == Invoice.STATUS_OPEN and r.due_date and r.due_date < today
        )

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
                    + (
                        " (OVERDUE)"
                        if r.status == Invoice.STATUS_OPEN and r.due_date and r.due_date < today
                        else ""
                    )
                )
                for r in rows[:10]
            )

        return {
            "success": True,
            "count": len(rows),
            "overdue_count": overdue_count,
            "invoices": InvoiceSerializer(rows, many=True).data,
            "message_for_user": message,
        }

    payload = get_or_set(cache_key, _INVOICES_CACHE_TTL, _compute_invoices_payload)
    _remember_invoices_cache_key(restaurant.id, cache_key)
    return Response(payload, status=status.HTTP_200_OK)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_update_invoice_bank_payment_status(request):
    """
    POST /api/finance/agent/invoices/payment-status/

    Update bank transfer / cheque lifecycle without marking fully paid.
    Body: invoice_id OR vendor+invoice_number, bank_payment_status, bank_payment_note, reference
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
        vendor = str(_get_first(data, "vendor", "vendor_name") or "").strip()
        invoice_number = str(_get_first(data, "invoice_number", "invoiceNumber") or "").strip()
        qs = Invoice.objects.filter(restaurant=restaurant).exclude(status=Invoice.STATUS_VOIDED)
        if vendor:
            qs = qs.filter(vendor_name__icontains=vendor)
        if invoice_number:
            qs = qs.filter(invoice_number__iexact=invoice_number)
        invoice = qs.order_by("-due_date").first()

    if invoice is None:
        return Response(
            {
                "success": False,
                "message_for_user": "I couldn't find that invoice to update payment status.",
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    raw_status = str(_get_first(data, "bank_payment_status", "bankPaymentStatus", "status") or "").upper()
    valid = {c[0] for c in Invoice.BANK_PAYMENT_STATUS_CHOICES}
    if raw_status not in valid:
        if raw_status in ("SENT", "TRANSFERRED", "ORDERED"):
            raw_status = Invoice.BANK_PAYMENT_INITIATED
        elif raw_status in ("PAID", "CLEARED", "RECEIVED"):
            raw_status = Invoice.BANK_PAYMENT_CLEARED
        elif raw_status in ("BOUNCED", "REJECTED"):
            raw_status = Invoice.BANK_PAYMENT_FAILED
        else:
            raw_status = Invoice.BANK_PAYMENT_INITIATED

    note = str(_get_first(data, "bank_payment_note", "bankPaymentNote", "note") or "")[:255]
    reference = str(_get_first(data, "reference", "payment_reference") or "")
    invoice.bank_payment_status = raw_status
    if note:
        invoice.bank_payment_note = note
    if reference:
        invoice.payment_reference = reference[:120]
    if raw_status == Invoice.BANK_PAYMENT_CLEARED and invoice.status == Invoice.STATUS_OPEN:
        invoice.mark_paid(method=invoice.payment_method or "BANK_TRANSFER", reference=reference, user=acting_user)
        invoice.refresh_from_db()
        invoice.bank_payment_status = raw_status
        if note:
            invoice.bank_payment_note = note
        invoice.save(update_fields=["bank_payment_status", "bank_payment_note", "updated_at"])
    else:
        invoice.save(update_fields=["bank_payment_status", "bank_payment_note", "payment_reference", "updated_at"])

    labels = dict(Invoice.BANK_PAYMENT_STATUS_CHOICES)
    return Response(
        {
            "success": True,
            "invoice": InvoiceSerializer(invoice).data,
            "message_for_user": (
                f"✓ {invoice.vendor_name} invoice — bank payment status: "
                f"{labels.get(invoice.bank_payment_status, invoice.bank_payment_status)}."
            ),
        }
    )


def _find_invoice(restaurant, data: dict) -> Invoice | None:
    invoice_id = _get_first(data, "invoice_id", "invoiceId", "id")
    if invoice_id:
        inv = Invoice.objects.filter(restaurant=restaurant, id=invoice_id).first()
        if inv:
            return inv
    vendor = str(_get_first(data, "vendor", "vendor_name") or "").strip()
    invoice_number = str(_get_first(data, "invoice_number", "invoiceNumber") or "").strip()
    qs = Invoice.objects.filter(restaurant=restaurant).exclude(status=Invoice.STATUS_VOIDED)
    if vendor:
        qs = qs.filter(vendor_name__icontains=vendor)
    if invoice_number:
        qs = qs.filter(invoice_number__iexact=invoice_number)
    if vendor or invoice_number:
        return qs.order_by("-due_date").first()
    return None


@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_match_invoice_po(request):
    """
    POST /api/finance/agent/invoices/match-po/

    Suggest purchase orders that may match an invoice (vendor + amount).
    Body: invoice_id OR vendor + invoice_number
    """
    from scheduling.views_agent import _resolve_restaurant_for_agent
    from .po_match import suggest_and_record_status

    restaurant, _, err = _resolve_restaurant_for_agent(request)
    if err:
        return Response({"success": False, "error": err["error"]}, status=err["status"])

    data = request.data if isinstance(getattr(request, "data", None), dict) else {}
    invoice = _find_invoice(restaurant, data)
    if invoice is None:
        return Response(
            {
                "success": False,
                "message_for_user": "I couldn't find that invoice to match against POs.",
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    result = suggest_and_record_status(invoice)
    suggestions = result.get("suggestions") or []
    if invoice.purchase_order_id:
        msg = (
            f"Invoice from {invoice.vendor_name} is already linked to PO "
            f"{str(invoice.purchase_order_id)[:8]}."
        )
    elif not suggestions:
        msg = (
            f"No close PO matches for {invoice.vendor_name} "
            f"({invoice.amount} {invoice.currency}). "
            "Create or receive the purchase order first, then try again."
        )
    else:
        top = suggestions[0]
        msg = (
            f"Best match for {invoice.vendor_name}: PO {top['purchase_order_id'][:8]}… "
            f"({top['supplier_name']}, {top['total_amount']}, score {top['score']}). "
            "Say confirm to link it, or pick another suggestion."
        )

    return Response(
        {
            "success": True,
            "invoice": InvoiceSerializer(invoice).data,
            "message_for_user": msg,
            **result,
        }
    )


@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_confirm_invoice_po_match(request):
    """
    POST /api/finance/agent/invoices/confirm-po-match/

    Body: invoice_id, purchase_order_id
    """
    from inventory.models import PurchaseOrder
    from scheduling.views_agent import _resolve_restaurant_for_agent
    from .po_match import apply_po_match

    restaurant, _, err = _resolve_restaurant_for_agent(request)
    if err:
        return Response({"success": False, "error": err["error"]}, status=err["status"])

    data = request.data if isinstance(getattr(request, "data", None), dict) else {}
    invoice = _find_invoice(restaurant, data)
    po_id = _get_first(data, "purchase_order_id", "purchaseOrderId", "po_id", "poId")
    if invoice is None or not po_id:
        return Response(
            {
                "success": False,
                "message_for_user": "I need the invoice and the purchase_order_id to confirm.",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    po = PurchaseOrder.objects.filter(restaurant=restaurant, id=po_id).first()
    if po is None:
        return Response(
            {
                "success": False,
                "message_for_user": "I couldn't find that purchase order.",
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    try:
        conf = _coerce_decimal(_get_first(data, "confidence", "match_confidence"))
        apply_po_match(
            invoice,
            po,
            confidence=float(conf) if conf is not None else 1.0,
        )
    except ValueError as e:
        return Response(
            {"success": False, "message_for_user": str(e)},
            status=status.HTTP_400_BAD_REQUEST,
        )

    return Response(
        {
            "success": True,
            "invoice": InvoiceSerializer(invoice).data,
            "message_for_user": (
                f"✓ Linked {invoice.vendor_name} invoice to PO "
                f"{str(po.id)[:8]}… ({po.supplier.name if po.supplier_id else 'supplier'})."
            ),
        }
    )


# ---------------------------------------------------------------------------
# PayGuard — payment approval ladder
# ---------------------------------------------------------------------------

@api_view(["GET", "POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_payment_approval(request):
    """
    GET  — list pending approvals / policy
    POST — action: start | approve | reject | get_policy | set_policy
    """
    from scheduling.views_agent import _resolve_restaurant_for_agent
    from finance.models import InvoicePaymentApproval
    from finance.payment_approval import (
        act_on_approval,
        get_policy,
        save_policy,
        serialize_approval,
        serialize_policy_for_ui,
        start_payment_approval,
    )

    restaurant, acting_user, err = _resolve_restaurant_for_agent(request)
    if err:
        return Response({"success": False, "error": err["error"]}, status=err["status"])

    data = request.data if isinstance(getattr(request, "data", None), dict) else {}
    action = str(_get_first(data, "action") or "").strip().lower()

    if request.method == "GET" or action in ("list", "pending", ""):
        if action in ("get_policy", "policy"):
            return Response(
                {"success": True, "policy": serialize_policy_for_ui(get_policy(restaurant))}
            )
        qs = (
            InvoicePaymentApproval.objects.filter(
                restaurant=restaurant, status=InvoicePaymentApproval.STATUS_PENDING
            )
            .select_related("invoice", "requested_by")
            .prefetch_related("steps")
            .order_by("started_at")[:20]
        )
        rows = []
        for a in qs:
            row = serialize_approval(a)
            inv = a.invoice
            row["invoice"] = {
                "id": str(inv.id),
                "vendor_name": inv.vendor_name,
                "amount": str(inv.amount),
                "currency": inv.currency,
                "invoice_number": inv.invoice_number,
            }
            row["requested_by_name"] = (
                f"{a.requested_by.first_name} {a.requested_by.last_name}".strip()
                if a.requested_by
                else None
            )
            rows.append(row)
        return Response(
            {
                "success": True,
                "count": len(rows),
                "approvals": rows,
                "policy_enabled": bool(get_policy(restaurant).get("enabled")),
                "message_for_user": (
                    f"{len(rows)} payment(s) waiting on the PayGuard ladder."
                    if rows
                    else "No payments waiting for approval right now."
                ),
            }
        )

    if action == "set_policy":
        policy_in = data.get("policy") if isinstance(data.get("policy"), dict) else data
        saved = save_policy(restaurant, policy_in)
        return Response(
            {
                "success": True,
                "policy": saved,
                "message_for_user": "✓ PayGuard ladder updated.",
            }
        )

    if action == "get_policy":
        return Response(
            {"success": True, "policy": serialize_policy_for_ui(get_policy(restaurant))}
        )

    invoice = None
    try:
        invoice = _find_invoice(restaurant, data)
    except Exception:
        invoice = None
    if invoice is None:
        invoice_id = _get_first(data, "invoice_id", "invoiceId", "id")
        if invoice_id:
            invoice = Invoice.objects.filter(restaurant=restaurant, id=invoice_id).first()

    if action == "start":
        if invoice is None:
            return Response(
                {
                    "success": False,
                    "message_for_user": "Which invoice should I submit for PayGuard approval?",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        result = start_payment_approval(invoice=invoice, requested_by=acting_user)
        return Response(result, status=200 if result.get("success") else 400)

    if action in ("approve", "reject"):
        if invoice is None:
            # Try first pending the actor can clear
            pending = (
                InvoicePaymentApproval.objects.filter(
                    restaurant=restaurant, status=InvoicePaymentApproval.STATUS_PENDING
                )
                .select_related("invoice")
                .order_by("started_at")
                .first()
            )
            invoice = pending.invoice if pending else None
        if invoice is None:
            return Response(
                {
                    "success": False,
                    "message_for_user": "I couldn't find a pending payment approval.",
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        result = act_on_approval(
            invoice=invoice,
            actor=acting_user,
            action=action,
            note=str(_get_first(data, "note", "notes") or ""),
        )
        return Response(result, status=200 if result.get("success") else 400)

    return Response(
        {
            "success": False,
            "message_for_user": "Use action start, approve, reject, list, or get_policy.",
        },
        status=status.HTTP_400_BAD_REQUEST,
    )
