"""
Auto-draft purchase orders from a prep-list shortage.

Groups ``ingredient_prep_list`` entries by supplier, rounds each quantity up to
the item's pack size / minimum order quantity (done upstream in
:func:`pos.forecast.suggest_order_qty`) and writes one
:class:`inventory.PurchaseOrder` + its line items per supplier.

Endpoint: ``POST /api/pos/prep-list/auto-po/``
"""

from __future__ import annotations

from decimal import Decimal
from datetime import timedelta
from typing import Dict, List

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from inventory.models import InventoryItem, PurchaseOrder, PurchaseOrderItem
from .integrations import IntegrationManager


def _as_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def auto_purchase_orders(request):
    """Create :class:`PurchaseOrder` rows (status=PENDING) for every supplier
    that has at least one shortage in the computed prep list.

    Accepts ``date`` or ``start_date``/``end_date`` (same semantics as
    ``/api/pos/prep-list/``). Returns the IDs of created POs and a list of
    ingredients that couldn't be drafted (no inventory row, no supplier, etc.).
    """
    restaurant = request.user.restaurant
    if not restaurant:
        return Response({"error": "No restaurant associated"}, status=status.HTTP_400_BAD_REQUEST)

    body = request.data or {}
    date_str = body.get("date") or request.query_params.get("date")
    start_str = body.get("start_date") or request.query_params.get("start_date")
    end_str = body.get("end_date") or request.query_params.get("end_date")
    target_date = parse_date(date_str) if date_str else None
    target_start = parse_date(start_str) if start_str else None
    target_end = parse_date(end_str) if end_str else None

    if target_start and target_end:
        prep = IntegrationManager.generate_prep_list(
            restaurant,
            target_date=target_date,
            target_start_date=target_start,
            target_end_date=target_end,
        )
    else:
        prep = IntegrationManager.generate_prep_list(restaurant, target_date)

    if not prep.get("success"):
        return Response(prep, status=status.HTTP_400_BAD_REQUEST)

    ingredients = prep.get("ingredient_prep_list") or []
    shortages = [i for i in ingredients if (i.get("gap") or 0) > 0]
    if not shortages:
        return Response(
            {
                "success": True,
                "message": "No shortages to order — you're fully stocked.",
                "created_orders": [],
                "skipped": [],
                "target_date": prep.get("target_date"),
                "target_end_date": prep.get("target_end_date"),
            }
        )

    # Group shortages by supplier_id. Items without a supplier go to the
    # skipped bucket so the frontend can nudge the operator to fix the mapping.
    by_supplier: Dict[str, List[dict]] = {}
    skipped: List[dict] = []
    for item in shortages:
        supplier_id = item.get("supplier_id")
        inventory_item_id = item.get("inventory_item_id")
        if not supplier_id or not inventory_item_id:
            skipped.append({
                "ingredient": item.get("ingredient"),
                "reason": (
                    "no_supplier" if not supplier_id else "no_inventory_item"
                ),
                "needed": item.get("needed"),
                "unit": item.get("unit"),
            })
            continue
        by_supplier.setdefault(supplier_id, []).append(item)

    if not by_supplier:
        return Response(
            {
                "success": False,
                "message": (
                    "Every shortage is missing a supplier or an InventoryItem. "
                    "Link each ingredient to a supplier in Inventory → Items and retry."
                ),
                "created_orders": [],
                "skipped": skipped,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Pull inventory_item rows once — avoids a query per line in the hot loop.
    inv_items = InventoryItem.objects.filter(
        restaurant=restaurant,
        id__in=[item["inventory_item_id"] for items in by_supplier.values() for item in items],
    ).select_related("supplier")
    inv_by_id = {str(i.id): i for i in inv_items}

    today = timezone.now().date()
    created_orders: List[dict] = []

    with transaction.atomic():
        for supplier_id, items in by_supplier.items():
            # Resolve supplier lead-time for expected_delivery_date.
            first_item = next((inv_by_id[i["inventory_item_id"]] for i in items if i.get("inventory_item_id") in inv_by_id), None)
            supplier = first_item.supplier if first_item else None
            if supplier is None:
                continue
            lead = int(supplier.lead_time_days or 0)
            expected = today + timedelta(days=lead) if lead > 0 else None

            po = PurchaseOrder.objects.create(
                restaurant=restaurant,
                supplier=supplier,
                expected_delivery_date=expected,
                status="PENDING",
                created_by=request.user,
            )
            total = Decimal("0.00")
            line_payload: List[dict] = []
            for item in items:
                inv = inv_by_id.get(item.get("inventory_item_id"))
                if inv is None:
                    skipped.append({
                        "ingredient": item.get("ingredient"),
                        "reason": "inventory_item_not_found",
                    })
                    continue
                qty = _as_decimal(item.get("suggested_order_qty") or item.get("gap") or 0)
                if qty <= 0:
                    continue
                unit_price = _as_decimal(inv.cost_per_unit or 0)
                line_total = (qty * unit_price).quantize(Decimal("0.01"))
                PurchaseOrderItem.objects.create(
                    purchase_order=po,
                    inventory_item=inv,
                    quantity=qty,
                    unit_price=unit_price,
                    total_price=line_total,
                )
                total += line_total
                line_payload.append({
                    "inventory_item_id": str(inv.id),
                    "ingredient": inv.name,
                    "quantity": float(qty),
                    "unit": inv.unit,
                    "unit_price": float(unit_price),
                    "line_total": float(line_total),
                })

            # Keep PO.total_amount in sync so the list view doesn't need to
            # re-aggregate after this commit.
            po.total_amount = total
            po.save(update_fields=["total_amount"])

            created_orders.append({
                "id": str(po.id),
                "supplier_id": str(supplier.id),
                "supplier_name": supplier.name,
                "expected_delivery_date": expected.isoformat() if expected else None,
                "total_amount": float(total),
                "currency": restaurant.currency or "MAD",
                "items": line_payload,
            })

    return Response(
        {
            "success": True,
            "message": (
                f"Drafted {len(created_orders)} purchase order(s) covering "
                f"{sum(len(o['items']) for o in created_orders)} ingredient(s). "
                "Review and send from Inventory → Purchase Orders."
            ),
            "created_orders": created_orders,
            "skipped": skipped,
            "target_date": prep.get("target_date"),
            "target_end_date": prep.get("target_end_date"),
        },
        status=status.HTTP_201_CREATED,
    )
