"""
Light purchase-order ↔ invoice matching.

Suggests open POs whose supplier name is similar to the invoice vendor and
whose total_amount is within a tolerance of the invoice amount.
"""
from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any

from django.db.models import Q
from django.utils import timezone

from inventory.models import PurchaseOrder

from .models import Invoice

_DEFAULT_AMOUNT_TOLERANCE = Decimal("0.05")  # 5%
_DEFAULT_NAME_THRESHOLD = 0.55
_DATE_WINDOW_DAYS = 45


def _norm_name(value: str) -> str:
    s = (value or "").lower().strip()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _name_score(a: str, b: str) -> float:
    na, nb = _norm_name(a), _norm_name(b)
    if not na or not nb:
        return 0.0
    if na == nb or na in nb or nb in na:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def _amount_close(po_total: Decimal, inv_amount: Decimal, tol: Decimal) -> bool:
    if inv_amount <= 0:
        return False
    diff = abs(Decimal(str(po_total)) - Decimal(str(inv_amount)))
    return diff <= (Decimal(str(inv_amount)) * tol)


def suggest_po_matches(
    invoice: Invoice,
    *,
    limit: int = 5,
    amount_tolerance: Decimal = _DEFAULT_AMOUNT_TOLERANCE,
    name_threshold: float = _DEFAULT_NAME_THRESHOLD,
) -> list[dict[str, Any]]:
    """Return ranked PO suggestions for an invoice (does not persist)."""
    if invoice.purchase_order_id:
        po = invoice.purchase_order
        return [
            {
                "purchase_order_id": str(po.id),
                "supplier_name": po.supplier.name if po.supplier_id else "",
                "total_amount": float(po.total_amount or 0),
                "status": po.status,
                "order_date": po.order_date.isoformat() if po.order_date else None,
                "score": 1.0,
                "already_linked": True,
                "reasons": ["Already linked to this invoice"],
            }
        ]

    today = timezone.now().date()
    window_start = today - timedelta(days=_DATE_WINDOW_DAYS)

    pos = (
        PurchaseOrder.objects.filter(restaurant_id=invoice.restaurant_id)
        .exclude(status="CANCELLED")
        .filter(Q(status__in=["PENDING", "ORDERED", "RECEIVED"]))
        .filter(order_date__gte=window_start)
        .select_related("supplier")
        .order_by("-order_date")[:80]
    )

    suggestions: list[dict[str, Any]] = []
    for po in pos:
        supplier = po.supplier.name if po.supplier_id else ""
        name_s = _name_score(invoice.vendor_name, supplier)
        amt_ok = _amount_close(po.total_amount or Decimal("0"), invoice.amount, amount_tolerance)
        if name_s < name_threshold and not amt_ok:
            continue
        if name_s < 0.35:
            continue

        reasons = []
        score = name_s * 0.6
        if amt_ok:
            score += 0.35
            reasons.append("Amount within 5%")
        elif invoice.amount and po.total_amount:
            pct = float(
                abs(Decimal(str(po.total_amount)) - invoice.amount) / invoice.amount * 100
            )
            reasons.append(f"Amount differs by {pct:.1f}%")
        if name_s >= 0.8:
            reasons.append("Supplier name strong match")
        elif name_s >= name_threshold:
            reasons.append("Supplier name partial match")

        # Prefer ORDERED/RECEIVED slightly
        if po.status in ("ORDERED", "RECEIVED"):
            score += 0.05

        suggestions.append(
            {
                "purchase_order_id": str(po.id),
                "supplier_name": supplier,
                "total_amount": float(po.total_amount or 0),
                "status": po.status,
                "order_date": po.order_date.isoformat() if po.order_date else None,
                "score": round(min(score, 1.0), 3),
                "already_linked": False,
                "reasons": reasons or ["Possible match"],
            }
        )

    suggestions.sort(key=lambda s: -s["score"])
    return suggestions[: max(1, min(int(limit or 5), 20))]


def apply_po_match(invoice: Invoice, purchase_order: PurchaseOrder, *, confidence: float | None = None) -> Invoice:
    if purchase_order.restaurant_id != invoice.restaurant_id:
        raise ValueError("Purchase order belongs to a different restaurant")
    invoice.purchase_order = purchase_order
    invoice.match_status = Invoice.MATCH_CONFIRMED
    invoice.match_confidence = Decimal(str(round(confidence if confidence is not None else 1.0, 3)))
    invoice.save(update_fields=["purchase_order", "match_status", "match_confidence", "updated_at"])
    return invoice


def suggest_and_record_status(invoice: Invoice) -> dict[str, Any]:
    """Rank PO candidates and stamp SUGGESTED status (no silent auto-confirm)."""
    suggestions = suggest_po_matches(invoice)
    if invoice.purchase_order_id:
        return {
            "invoice_id": str(invoice.id),
            "suggestions": suggestions,
            "match_status": invoice.match_status,
            "purchase_order_id": str(invoice.purchase_order_id),
        }

    if suggestions:
        invoice.match_status = Invoice.MATCH_SUGGESTED
        invoice.match_confidence = Decimal(str(suggestions[0]["score"]))
        invoice.save(update_fields=["match_status", "match_confidence", "updated_at"])
    else:
        invoice.match_status = Invoice.MATCH_UNMATCHED
        invoice.match_confidence = None
        invoice.save(update_fields=["match_status", "match_confidence", "updated_at"])

    return {
        "invoice_id": str(invoice.id),
        "suggestions": suggestions,
        "match_status": invoice.match_status,
        "purchase_order_id": None,
    }
