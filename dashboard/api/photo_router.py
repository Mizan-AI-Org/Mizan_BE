"""
Photo-to-action router endpoint.

A single multipart/form-data endpoint Miya hits after the manager
sends an image: classify the photo and (optionally) auto-create the
follow-up record (invoice, maintenance request, incident, etc.).

POST /api/dashboard/agent/parse-photo/
   image          required (file)
   auto_create    optional bool, default true
   note           optional manager-supplied caption (used as a hint for
                  ambiguous photos and stored on whatever record we create)

Response (always 200 unless the photo itself can't be read):
  {
    success: true,
    classification: {category, confidence, summary, suggested_action, fields},
    action_taken: {
        type: "invoice" | "staff_request" | "incident" | "schedule_pending" | "none",
        record_id: str | null,
        message_for_user: str
    }
  }

Auto-creation rules
-------------------
- ``invoice_or_receipt``  + confidence >= 0.55 + amount + due_date + vendor
                          -> create Invoice (status=OPEN)
- ``equipment_issue``     + confidence >= 0.5
                          -> create StaffRequest(category=MAINTENANCE)
- ``incident``            + confidence >= 0.5
                          -> create reporting.Incident
- ``schedule``            -> DON'T auto-import (scheduling has its own flow)
- ``id_or_certification`` -> DON'T auto-create (need a target staff member)
- everything else         -> just return the classification.
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from rest_framework import permissions, status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    parser_classes,
    permission_classes,
)
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from scheduling.photo_router_service import parse_photo

logger = logging.getLogger(__name__)


def _to_decimal(raw) -> Decimal | None:
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _coerce_severity(raw) -> str:
    """Map vision severity to MAINTENANCE / incident severity choices."""
    s = str(raw or "").lower()
    if s in ("high", "critical", "urgent", "severe"):
        return "HIGH"
    if s in ("medium", "moderate"):
        return "MEDIUM"
    return "LOW"


def _action_envelope(
    *,
    type_: str,
    record_id: str | None,
    message: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "type": type_,
        "record_id": record_id,
        "message_for_user": message,
    }
    if extra:
        out.update(extra)
    return out


def _try_create_invoice(restaurant, fields: dict[str, Any], note: str, summary: str):
    """Build an Invoice from vision fields. Returns (invoice_or_None, message)."""
    from finance.models import Invoice

    vendor = (fields.get("vendor") or "").strip()
    amount = _to_decimal(fields.get("amount"))
    due_date = fields.get("due_date") or None
    if not (vendor and amount and due_date):
        return None, (
            "I can see this looks like an invoice but I'm missing some details. "
            f"Vendor: {vendor or 'unknown'}, "
            f"amount: {amount or 'unknown'}, "
            f"due date: {due_date or 'unknown'}. Want me to log it anyway?"
        )

    invoice_number = (fields.get("invoice_number") or "").strip()
    if invoice_number:
        existing = Invoice.objects.filter(
            restaurant=restaurant,
            vendor_name__iexact=vendor,
            invoice_number=invoice_number,
        ).first()
        if existing:
            return existing, (
                f"That invoice from {vendor} (#{invoice_number}) is already in the books - "
                f"due {existing.due_date}, status {existing.status}."
            )

    try:
        currency = (fields.get("currency") or "").strip().upper() or "USD"
        notes = (note or summary or "").strip()
        invoice = Invoice.objects.create(
            restaurant=restaurant,
            vendor_name=vendor,
            invoice_number=invoice_number,
            amount=amount,
            currency=currency,
            issue_date=fields.get("issue_date") or None,
            due_date=due_date,
            status="OPEN",
            notes=notes,
        )
    except Exception:
        logger.exception("parse_photo: failed to auto-create invoice")
        return None, "I tried to log this invoice but the save failed. Please try from the Finance page."

    return invoice, (
        f"Logged invoice from {vendor}: {currency} {amount} due {due_date}."
        + (f" (Invoice #{invoice_number})" if invoice_number else "")
    )


def _try_create_maintenance_request(
    restaurant, acting_user, fields: dict[str, Any], summary: str, note: str
):
    from staff.models import StaffRequest

    equipment = (fields.get("equipment") or "").strip()
    location_hint = (fields.get("location_hint") or "").strip()
    severity = _coerce_severity(fields.get("severity"))

    subject = equipment or "Equipment issue from photo"
    if location_hint:
        subject = f"{subject} ({location_hint})"

    description_parts = [summary]
    if note:
        description_parts.append(f"Manager note: {note}")
    description = "\n\n".join(p for p in description_parts if p)

    try:
        sr = StaffRequest.objects.create(
            restaurant=restaurant,
            staff=acting_user if acting_user else None,
            subject=subject[:255],
            description=description,
            category="MAINTENANCE",
            priority=severity if severity in ("LOW", "MEDIUM", "HIGH") else "MEDIUM",
            status="PENDING",
            source="photo_router",
        )
    except Exception:
        logger.exception("parse_photo: failed to auto-create maintenance request")
        return None, "I couldn't open a maintenance request automatically - please try from the Requests page."

    return sr, (
        f"Opened a {severity.lower()}-priority maintenance request: \"{subject}\". "
        "I'll route it to the person on duty."
    )


def _try_create_incident(restaurant, acting_user, fields: dict[str, Any], summary: str, note: str):
    """Log to ``reporting.Incident`` when available; fall back to a staff request."""
    severity = _coerce_severity(fields.get("severity"))
    incident_type = (fields.get("incident_type") or "other").strip().lower()
    injury = bool(fields.get("injury"))

    description_parts = [summary]
    if note:
        description_parts.append(f"Manager note: {note}")
    if injury:
        description_parts.append("Injury reported.")
    description = "\n\n".join(p for p in description_parts if p)

    incident_priority = "CRITICAL" if (injury and severity == "HIGH") else (
        "HIGH" if severity == "HIGH" else ("MEDIUM" if severity == "MEDIUM" else "LOW")
    )

    try:
        from reporting.models import Incident

        report = Incident.objects.create(
            restaurant=restaurant,
            reporter=acting_user if acting_user else None,
            title=(summary or f"Incident: {incident_type}")[:255],
            description=description,
            category=incident_type[:100],
            priority=incident_priority,
            status="OPEN",
        )
        return ("incident", str(report.id), (
            f"Logged a {incident_priority.lower()}-priority {incident_type} incident."
        )), None
    except Exception:
        logger.exception("parse_photo: Incident create failed, falling back to staff request")

    from staff.models import StaffRequest

    try:
        sr = StaffRequest.objects.create(
            restaurant=restaurant,
            staff=acting_user if acting_user else None,
            subject=(summary or f"Incident: {incident_type}")[:255],
            description=description,
            category="OPERATIONS",
            priority="HIGH" if severity == "HIGH" else "MEDIUM",
            status="ESCALATED",
            source="photo_router",
        )
    except Exception:
        logger.exception("parse_photo: failed to escalate incident as staff request")
        return None, "I couldn't auto-log this incident. Please report it from the Incidents page."

    return ("staff_request", str(sr.id), (
        f"This looks like a {incident_type} incident - I've escalated it to the manager queue."
    )), None


@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
@parser_classes([MultiPartParser, FormParser])
def agent_parse_photo(request):
    """Classify an uploaded photo and (optionally) take the matching action."""
    from scheduling.views_agent import _resolve_restaurant_for_agent

    restaurant, acting_user, err = _resolve_restaurant_for_agent(request)
    if err:
        return Response({"success": False, "error": err["error"]}, status=err["status"])

    image_file = request.FILES.get("image") or request.FILES.get("photo")
    if not image_file:
        return Response(
            {
                "success": False,
                "error": "Missing image",
                "message_for_user": "Please attach the photo and I'll take a look.",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    incoming_ct = (getattr(image_file, "content_type", "") or "").lower()
    incoming_name = (getattr(image_file, "name", "") or "").lower()
    is_image_mime = incoming_ct.startswith("image/")
    is_image_ext = incoming_name.endswith(
        (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".heif")
    )
    if not (is_image_mime or is_image_ext):
        return Response(
            {
                "success": False,
                "status": "wrong_tool",
                "code": "USE_PARSE_DOCUMENT",
                "error": f"parse_photo only handles images, got content_type={incoming_ct or 'unknown'}",
                "miya_directive": (
                    "This is not an image (got "
                    f"{incoming_ct or 'unknown content type'}). DO NOT pretend to have parsed it. "
                    "If it's a PDF / Word / Excel / CSV / text file, call parse_document with the same "
                    "URL or bytes. If parse_document also can't read it, ask the user to type out the "
                    "key fields (vendor, amount, due date, invoice number) before you call record_invoice. "
                    "NEVER fabricate vendor / amount / invoice_number / due_date."
                ),
                "message_for_user": (
                    "I can only read images directly. If that file is a PDF or a Word/Excel document, "
                    "tell me the vendor, the amount, the due date, and the invoice number, and I'll log it."
                ),
            },
            status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )

    auto_create_raw = request.data.get("auto_create")
    auto_create = True
    if auto_create_raw is not None:
        auto_create = str(auto_create_raw).strip().lower() not in ("0", "false", "no", "off", "")

    note = str(request.data.get("note") or "").strip()

    try:
        image_bytes = image_file.read()
    except Exception:
        return Response(
            {"success": False, "error": "Could not read image"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    classification = parse_photo(
        image_bytes,
        content_type=getattr(image_file, "content_type", "image/jpeg") or "image/jpeg",
    )
    if classification.get("error"):
        return Response(
            {
                "success": False,
                "error": classification["error"],
                "classification": classification,
                "message_for_user": "I couldn't analyze that photo. Want to describe it instead?",
            },
            status=status.HTTP_502_BAD_GATEWAY,
        )

    category = classification["category"]
    confidence = float(classification.get("confidence") or 0.0)
    fields = classification.get("fields") or {}
    summary = classification.get("summary") or ""

    action_envelope = _action_envelope(
        type_="none",
        record_id=None,
        message=summary or "Got the photo, not sure what to do with it.",
    )

    if auto_create:
        if category == "invoice_or_receipt" and confidence >= 0.55:
            invoice, msg = _try_create_invoice(restaurant, fields, note, summary)
            if invoice:
                action_envelope = _action_envelope(
                    type_="invoice",
                    record_id=str(invoice.id),
                    message=msg,
                    extra={"invoice_status": invoice.status},
                )
            else:
                action_envelope = _action_envelope(
                    type_="invoice_pending",
                    record_id=None,
                    message=msg,
                )

        elif category == "equipment_issue" and confidence >= 0.5:
            sr, msg = _try_create_maintenance_request(
                restaurant, acting_user, fields, summary, note
            )
            if sr:
                action_envelope = _action_envelope(
                    type_="staff_request",
                    record_id=str(sr.id),
                    message=msg,
                    extra={"category": "MAINTENANCE", "priority": sr.priority},
                )

        elif category == "incident" and confidence >= 0.5:
            result, _err = _try_create_incident(
                restaurant, acting_user, fields, summary, note
            )
            if result:
                type_, rid, msg = result
                action_envelope = _action_envelope(
                    type_=type_,
                    record_id=str(rid) if rid else None,
                    message=msg,
                )

        elif category == "schedule":
            action_envelope = _action_envelope(
                type_="schedule_pending",
                record_id=None,
                message=(
                    "That looks like a staff schedule. Open the Schedule page > 'Import from photo' "
                    "and I'll parse the shifts in detail."
                ),
            )

        elif category == "id_or_certification":
            doc_type = (fields.get("document_type") or "document").strip()
            person = (fields.get("person_name") or "").strip()
            expiry = fields.get("expiry_date") or None
            who = f" for {person}" if person else ""
            when = f" (expires {expiry})" if expiry else ""
            action_envelope = _action_envelope(
                type_="document_pending",
                record_id=None,
                message=(
                    f"I see a {doc_type}{who}{when}. Tell me which staff member this belongs to "
                    "and I'll attach it to their HR profile."
                ),
            )

        elif category == "inventory":
            items = fields.get("items") or []
            count = len(items) if isinstance(items, list) else 0
            action_envelope = _action_envelope(
                type_="inventory_pending",
                record_id=None,
                message=(
                    f"I can read about {count} items off this photo. "
                    "Open Inventory > 'Stock count from photo' to commit the count."
                ),
            )

    return Response(
        {
            "success": True,
            "classification": classification,
            "action_taken": action_envelope,
            "message_for_user": action_envelope.get("message_for_user"),
        },
        status=status.HTTP_200_OK,
    )
