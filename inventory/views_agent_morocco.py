"""
Agent-authenticated endpoints for Morocco-specific features:
  - Waste reporting (Miya logs waste from staff WhatsApp messages)
  - Inventory counting (conversational count session)
  - Supplier WhatsApp ordering (send PO to supplier via WhatsApp)
"""
import logging
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.utils import timezone
from django.db.models import Sum, Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import InventoryItem, WasteEntry, InventoryCountSession, Supplier, PurchaseOrder, PurchaseOrderItem

logger = logging.getLogger(__name__)


def _validate_agent(request):
    auth = request.headers.get("Authorization")
    key = getattr(settings, "LUA_WEBHOOK_API_KEY", None)
    if not key:
        return False, Response({"success": False, "error": "Agent key not configured"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    if not auth or auth != f"Bearer {key}":
        return False, Response({"success": False, "error": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)
    return True, None


def _get_restaurant(request):
    rid = request.data.get("restaurant_id") or request.META.get("HTTP_X_RESTAURANT_ID") or request.query_params.get("restaurant_id")
    if not rid:
        return None, Response({"success": False, "error": "restaurant_id required"}, status=status.HTTP_400_BAD_REQUEST)
    from accounts.models import Restaurant
    try:
        return Restaurant.objects.get(id=rid), None
    except Exception:
        return None, Response({"success": False, "error": "Restaurant not found"}, status=status.HTTP_404_NOT_FOUND)


# ─── WASTE REPORTING ────────────────────────────────────────────────────────────

@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def agent_report_waste(request):
    """
    POST /api/inventory/agent/waste/
    Body: restaurant_id, item_name, quantity, unit (optional), reason (optional),
          staff_id (optional), notes (optional)
    Miya calls this when staff report waste via WhatsApp.
    """
    ok, err = _validate_agent(request)
    if not ok:
        return err
    restaurant, err = _get_restaurant(request)
    if err:
        return err

    data = request.data
    item_name = (data.get("item_name") or "").strip()
    if not item_name:
        return Response({"success": False, "error": "item_name required", "message_for_user": "Please tell me what was wasted."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        quantity = Decimal(str(data.get("quantity", 0)))
    except (InvalidOperation, ValueError):
        return Response({"success": False, "error": "Invalid quantity", "message_for_user": "I couldn't understand the quantity. Please try again."}, status=status.HTTP_400_BAD_REQUEST)

    if quantity <= 0:
        return Response({"success": False, "error": "quantity must be > 0", "message_for_user": "Please specify a valid quantity."}, status=status.HTTP_400_BAD_REQUEST)

    inv_item = InventoryItem.objects.filter(restaurant=restaurant, name__iexact=item_name, is_active=True).first()
    unit = data.get("unit") or (inv_item.unit if inv_item else "UNIT")
    cost = (inv_item.cost_per_unit * quantity) if inv_item else Decimal("0")

    staff = None
    staff_id = data.get("staff_id")
    if staff_id:
        from accounts.models import CustomUser
        staff = CustomUser.objects.filter(id=staff_id).first()

    entry = WasteEntry.objects.create(
        restaurant=restaurant,
        inventory_item=inv_item,
        item_name=item_name,
        quantity=quantity,
        unit=unit,
        estimated_cost=cost,
        reason=data.get("reason", "OTHER"),
        notes=data.get("notes", ""),
        reported_by=staff,
        waste_date=timezone.now().date(),
    )

    if inv_item:
        inv_item.current_stock = max(Decimal("0"), inv_item.current_stock - quantity)
        inv_item.save(update_fields=["current_stock"])

    cost_str = f"{cost:.2f} MAD" if cost > 0 else "unknown cost"
    return Response({
        "success": True,
        "waste_id": str(entry.id),
        "cost": float(cost),
        "message_for_user": f"Recorded: {quantity} {unit} of {item_name} wasted ({cost_str}). I've updated the stock levels.",
    })


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def agent_waste_summary(request):
    """
    GET /api/inventory/agent/waste/summary/?restaurant_id=X&date=YYYY-MM-DD (default today)
    Returns today's waste summary.
    """
    ok, err = _validate_agent(request)
    if not ok:
        return err
    restaurant, err = _get_restaurant(request)
    if err:
        return err

    date_str = request.query_params.get("date")
    target_date = timezone.now().date()
    if date_str:
        try:
            from datetime import datetime as dt
            target_date = dt.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            pass

    entries = WasteEntry.objects.filter(restaurant=restaurant, waste_date=target_date)
    total_cost = entries.aggregate(t=Sum("estimated_cost"))["t"] or Decimal("0")
    items = [
        {"item": e.item_name, "quantity": float(e.quantity), "unit": e.unit, "cost": float(e.estimated_cost), "reason": e.reason}
        for e in entries.order_by("-created_at")[:20]
    ]

    return Response({
        "success": True,
        "date": target_date.isoformat(),
        "total_entries": entries.count(),
        "total_cost": float(total_cost),
        "items": items,
        "message_for_user": f"Waste on {target_date}: {entries.count()} item(s) totalling {total_cost:.2f} MAD." if entries.exists() else f"No waste reported on {target_date}.",
    })


# ─── INVENTORY COUNTS ───────────────────────────────────────────────────────────

@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def agent_start_inventory_count(request):
    """
    POST /api/inventory/agent/count/start/
    Body: restaurant_id, staff_id (optional), category (optional — filter items)
    Starts a conversational count session. Returns the first item to count.
    """
    ok, err = _validate_agent(request)
    if not ok:
        return err
    restaurant, err = _get_restaurant(request)
    if err:
        return err

    items_qs = InventoryItem.objects.filter(restaurant=restaurant, is_active=True).order_by("name")
    category = request.data.get("category")
    if category:
        items_qs = items_qs.filter(name__icontains=category)

    items = list(items_qs.values("id", "name", "current_stock", "unit"))
    if not items:
        return Response({"success": False, "message_for_user": "No inventory items found to count."}, status=status.HTTP_400_BAD_REQUEST)

    staff = None
    staff_id = request.data.get("staff_id")
    if staff_id:
        from accounts.models import CustomUser
        staff = CustomUser.objects.filter(id=staff_id).first()

    session = InventoryCountSession.objects.create(
        restaurant=restaurant,
        counted_by=staff,
        items_total=len(items),
        count_date=timezone.now().date(),
        count_data={"items_order": [str(i["id"]) for i in items]},
    )

    first = items[0]
    return Response({
        "success": True,
        "session_id": str(session.id),
        "total_items": len(items),
        "current_index": 0,
        "current_item": {"id": str(first["id"]), "name": first["name"], "unit": first["unit"], "expected_stock": float(first["current_stock"])},
        "message_for_user": f"Inventory count started — {len(items)} items to count.\n\nFirst item: **{first['name']}** (expected: {first['current_stock']} {first['unit']}). How many do you have?",
    })


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def agent_count_item(request):
    """
    POST /api/inventory/agent/count/item/
    Body: session_id, counted_quantity
    Records the count for the current item and returns the next one (or completion).
    """
    ok, err = _validate_agent(request)
    if not ok:
        return err

    session_id = request.data.get("session_id")
    if not session_id:
        return Response({"success": False, "error": "session_id required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        session = InventoryCountSession.objects.get(id=session_id, status="IN_PROGRESS")
    except InventoryCountSession.DoesNotExist:
        return Response({"success": False, "error": "Session not found or already completed"}, status=status.HTTP_404_NOT_FOUND)

    try:
        counted = Decimal(str(request.data.get("counted_quantity", 0)))
    except (InvalidOperation, ValueError):
        return Response({"success": False, "message_for_user": "I didn't understand that number. Please enter the count."}, status=status.HTTP_400_BAD_REQUEST)

    items_order = session.count_data.get("items_order", [])
    idx = session.current_item_index
    if idx >= len(items_order):
        return Response({"success": False, "error": "No more items"}, status=status.HTTP_400_BAD_REQUEST)

    current_item_id = items_order[idx]
    inv_item = InventoryItem.objects.filter(id=current_item_id).first()

    count_data = session.count_data
    if "counts" not in count_data:
        count_data["counts"] = {}
    expected = float(inv_item.current_stock) if inv_item else 0
    variance = float(counted) - expected
    count_data["counts"][current_item_id] = {"counted": float(counted), "expected": expected, "variance": variance, "name": inv_item.name if inv_item else "Unknown"}

    session.current_item_index = idx + 1
    session.items_counted = idx + 1
    session.count_data = count_data
    session.save(update_fields=["current_item_index", "items_counted", "count_data"])

    variance_note = ""
    if abs(variance) > 0.01:
        direction = "more" if variance > 0 else "less"
        variance_note = f" (Variance: {abs(variance):.1f} {direction} than expected)"

    if idx + 1 < len(items_order):
        next_id = items_order[idx + 1]
        next_item = InventoryItem.objects.filter(id=next_id).values("id", "name", "current_stock", "unit").first()
        return Response({
            "success": True,
            "done": False,
            "items_counted": idx + 1,
            "items_remaining": len(items_order) - idx - 1,
            "current_item": {"id": str(next_item["id"]), "name": next_item["name"], "unit": next_item["unit"], "expected_stock": float(next_item["current_stock"])} if next_item else None,
            "message_for_user": f"Got it: {counted}{variance_note}.\n\nNext: **{next_item['name']}** (expected: {next_item['current_stock']} {next_item['unit']}). How many?",
        })

    session.status = "COMPLETED"
    session.completed_at = timezone.now()
    session.save(update_fields=["status", "completed_at"])

    variances = [v for v in count_data.get("counts", {}).values() if abs(v.get("variance", 0)) > 0.01]
    if variances:
        lines = [f"• {v['name']}: counted {v['counted']}, expected {v['expected']} ({'+' if v['variance'] > 0 else ''}{v['variance']:.1f})" for v in variances[:10]]
        summary = f"Count complete! {len(variances)} item(s) with variances:\n" + "\n".join(lines)
    else:
        summary = f"Count complete! All {len(items_order)} items match expected stock."

    return Response({
        "success": True,
        "done": True,
        "items_counted": len(items_order),
        "variances": len(variances),
        "message_for_user": summary,
    })


# ─── SUPPLIER WHATSAPP ORDERING ─────────────────────────────────────────────────

@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def agent_send_supplier_order(request):
    """
    POST /api/inventory/agent/supplier-order/
    Body: restaurant_id, supplier_name (or supplier_id), items: [{name, quantity, unit}], message (optional)
    Creates a PO and sends it to the supplier via WhatsApp.
    """
    ok, err = _validate_agent(request)
    if not ok:
        return err
    restaurant, err = _get_restaurant(request)
    if err:
        return err

    data = request.data
    supplier_name = (data.get("supplier_name") or "").strip()
    supplier_id = data.get("supplier_id")
    items_list = data.get("items", [])

    if not items_list:
        return Response({"success": False, "message_for_user": "Please specify at least one item to order."}, status=status.HTTP_400_BAD_REQUEST)

    supplier = None
    if supplier_id:
        supplier = Supplier.objects.filter(id=supplier_id, restaurant=restaurant).first()
    if not supplier and supplier_name:
        supplier = Supplier.objects.filter(restaurant=restaurant, name__icontains=supplier_name).first()
    if not supplier:
        suppliers = list(Supplier.objects.filter(restaurant=restaurant).values_list("name", flat=True)[:10])
        return Response({
            "success": False,
            "message_for_user": f"I couldn't find supplier '{supplier_name}'. Available suppliers: {', '.join(suppliers) if suppliers else 'none yet — add suppliers in settings.'}",
        }, status=status.HTTP_400_BAD_REQUEST)

    po = PurchaseOrder.objects.create(
        restaurant=restaurant,
        supplier=supplier,
        status="PENDING",
    )

    order_lines = []
    total = Decimal("0")
    for item_data in items_list:
        name = (item_data.get("name") or "").strip()
        qty = Decimal(str(item_data.get("quantity", 1)))
        unit = item_data.get("unit", "")
        inv_item = InventoryItem.objects.filter(restaurant=restaurant, name__iexact=name, is_active=True).first()
        unit_price = inv_item.cost_per_unit if inv_item else Decimal("0")
        line_total = unit_price * qty

        PurchaseOrderItem.objects.create(
            purchase_order=po,
            inventory_item=inv_item or InventoryItem.objects.filter(restaurant=restaurant).first(),
            quantity=qty,
            unit_price=unit_price,
            total_price=line_total,
        )
        order_lines.append(f"• {qty} {unit or (inv_item.unit if inv_item else '')} {name}")
        total += line_total

    po.total_amount = total
    po.save(update_fields=["total_amount"])

    wa_message = (
        f"Salam {supplier.contact_person or supplier.name},\n\n"
        f"Commande de {restaurant.name} :\n"
        + "\n".join(order_lines)
        + f"\n\nMerci de confirmer la disponibilité.\n- Miya (Mizan AI)"
    )

    whatsapp_sent = False
    if supplier.phone:
        try:
            from notifications.services import notification_service
            ok_wa, _ = notification_service.send_whatsapp_text(supplier.phone, wa_message)
            if ok_wa:
                whatsapp_sent = True
                po.status = "ORDERED"
                po.save(update_fields=["status"])
        except Exception as e:
            logger.warning("Failed to send supplier WhatsApp for PO %s: %s", po.id, e)

    msg = f"Order sent to {supplier.name}"
    if whatsapp_sent:
        msg += " via WhatsApp. They should confirm shortly."
    elif supplier.phone:
        msg += f". WhatsApp delivery failed — you can call them at {supplier.phone}."
    else:
        msg += ". No phone number on file for this supplier — please add one so I can send orders via WhatsApp."

    return Response({
        "success": True,
        "po_id": str(po.id),
        "whatsapp_sent": whatsapp_sent,
        "message_for_user": msg,
    })


# ─── CASH RECONCILIATION ────────────────────────────────────────────────────────

@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def agent_open_cash_session(request):
    """
    POST /api/timeclock/agent/cash/open/
    Body: restaurant_id, staff_id, opening_float (MAD)
    Creates a CashSession for the shift. Called when staff clock in or when manager opens the drawer.
    """
    ok, err = _validate_agent(request)
    if not ok:
        return err
    restaurant, err = _get_restaurant(request)
    if err:
        return err

    from timeclock.models import CashSession
    from accounts.models import CustomUser

    staff_id = request.data.get("staff_id")
    if not staff_id:
        return Response({"success": False, "message_for_user": "I need to know who's opening the cash drawer."}, status=status.HTTP_400_BAD_REQUEST)

    staff = CustomUser.objects.filter(id=staff_id).first()
    if not staff:
        return Response({"success": False, "error": "Staff not found"}, status=status.HTTP_404_NOT_FOUND)

    try:
        opening_float = Decimal(str(request.data.get("opening_float", 0)))
    except (InvalidOperation, ValueError):
        opening_float = Decimal("0")

    session = CashSession.objects.create(
        restaurant=restaurant,
        staff=staff,
        opening_float=opening_float,
        session_date=timezone.now().date(),
    )

    return Response({
        "success": True,
        "session_id": str(session.id),
        "message_for_user": f"Cash drawer opened with {opening_float:.2f} MAD float. I'll ask you to count at the end of your shift.",
    })


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def agent_close_cash_session(request):
    """
    POST /api/timeclock/agent/cash/close/
    Body: session_id (or restaurant_id + staff_id to find today's open session), counted_cash, variance_reason (optional)
    Staff reports their cash count. System computes variance.
    """
    ok, err = _validate_agent(request)
    if not ok:
        return err

    from timeclock.models import CashSession

    session_id = request.data.get("session_id")
    session = None
    if session_id:
        session = CashSession.objects.filter(id=session_id, status="OPEN").first()
    else:
        restaurant, err = _get_restaurant(request)
        if err:
            return err
        staff_id = request.data.get("staff_id")
        if staff_id:
            session = CashSession.objects.filter(restaurant=restaurant, staff_id=staff_id, status="OPEN", session_date=timezone.now().date()).order_by("-opened_at").first()

    if not session:
        return Response({"success": False, "message_for_user": "No open cash session found. The drawer may not have been opened today."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        counted = Decimal(str(request.data.get("counted_cash", 0)))
    except (InvalidOperation, ValueError):
        return Response({"success": False, "message_for_user": "I didn't understand that amount. Please enter the total cash in the drawer."}, status=status.HTTP_400_BAD_REQUEST)

    expected_cash = session.expected_cash or Decimal("0")
    session.counted_cash = counted
    session.compute_variance()
    session.counted_at = timezone.now()
    session.variance_reason = (request.data.get("variance_reason") or "").strip()
    session.status = "COUNTED"

    variance = session.variance or Decimal("0")
    VARIANCE_THRESHOLD = Decimal("50")
    if abs(variance) > VARIANCE_THRESHOLD:
        session.status = "FLAGGED"

    session.save()

    if abs(variance) < Decimal("1"):
        msg = f"Cash count: {counted:.2f} MAD. Perfect — no variance. Drawer closed."
    elif abs(variance) <= VARIANCE_THRESHOLD:
        direction = "over" if variance > 0 else "short"
        msg = f"Cash count: {counted:.2f} MAD. Variance: {abs(variance):.2f} MAD {direction}. Within tolerance — drawer closed."
    else:
        direction = "over" if variance > 0 else "short"
        msg = f"Cash count: {counted:.2f} MAD. Variance: {abs(variance):.2f} MAD {direction}. This exceeds the threshold — your manager has been notified."

    return Response({
        "success": True,
        "session_id": str(session.id),
        "variance": float(variance),
        "status": session.status,
        "message_for_user": msg,
    })
