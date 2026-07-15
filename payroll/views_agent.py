"""
Agent-authenticated payroll, compliance, HACCP, and delivery menu endpoints for Miya.
"""
from __future__ import annotations

import base64
import logging
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import permissions, status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from accounts.models import CustomUser
from payroll.models import ComplianceReminder, Payslip, TemperatureReading
from payroll.services.compliance_seed import morocco_compliance_templates
from payroll.services.delivery_menu import sync_delivery_menu
from payroll.services.hours import staff_hours_map_for_restaurant
from payroll.services.payslip_pdf import generate_payslip_for_staff

logger = logging.getLogger(__name__)


def _get_first(data, *keys):
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def _parse_period(data) -> tuple[date | None, date | None]:
    start = parse_date(str(_get_first(data, "period_start", "periodStart", "start_date", "startDate") or ""))
    end = parse_date(str(_get_first(data, "period_end", "periodEnd", "end_date", "endDate") or ""))
    month = _get_first(data, "month")
    year = _get_first(data, "year")
    if not start and month and year:
        try:
            m, y = int(month), int(year)
            start = date(y, m, 1)
            if m == 12:
                end = date(y, 12, 31)
            else:
                end = date(y, m + 1, 1) - timedelta(days=1)
        except (ValueError, TypeError):
            pass
    if not start and not end:
        today = timezone.now().date()
        start = today.replace(day=1)
        end = today
    return start, end


def _short_ref(record_id) -> str:
    return str(record_id).replace("-", "")[-8:].upper()


@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_generate_payslips(request):
    """
    POST /api/payroll/agent/payslips/generate/

    Body: period_start, period_end OR month+year, optional staff_id / staff_name, format=pdf|json
    """
    from scheduling.views_agent import _resolve_restaurant_for_agent

    restaurant, acting_user, err = _resolve_restaurant_for_agent(request)
    if err:
        return Response({"success": False, "error": err["error"]}, status=err["status"])

    data = request.data if isinstance(getattr(request, "data", None), dict) else {}
    period_start, period_end = _parse_period(data)
    if not period_start or not period_end:
        return Response(
            {
                "success": False,
                "message_for_user": "I need a pay period (e.g. month=3&year=2026 or period_start/period_end).",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    staff_id = _get_first(data, "staff_id", "staffId", "user_id", "userId")
    staff_name = str(_get_first(data, "staff_name", "staffName", "name") or "").strip()
    want_pdf = str(_get_first(data, "format", "output") or "json").lower() == "pdf"

    staff_qs = CustomUser.objects.filter(restaurant=restaurant, is_active=True).exclude(role="SUPER_ADMIN")
    if staff_id:
        staff_qs = staff_qs.filter(id=staff_id)
    elif staff_name:
        parts = staff_name.split()
        if len(parts) >= 2:
            staff_qs = staff_qs.filter(first_name__icontains=parts[0], last_name__icontains=parts[-1])
        else:
            staff_qs = staff_qs.filter(
                Q(first_name__icontains=staff_name) | Q(last_name__icontains=staff_name)
            )

    hours_map = staff_hours_map_for_restaurant(restaurant, period_start, period_end)
    generated = []
    pdf_response = None

    for staff in staff_qs.iterator():
        sid = str(staff.id)
        hours_override = hours_map.get(sid)
        payslip, created, pdf_bytes = generate_payslip_for_staff(
            staff=staff,
            restaurant=restaurant,
            period_start=period_start,
            period_end=period_end,
            hours=hours_override,
            acting_user=acting_user,
        )
        generated.append(
            {
                "staff_id": sid,
                "staff_name": staff.get_full_name() or staff.email,
                "payslip_id": str(payslip.id),
                "ref": _short_ref(payslip.id),
                "hours": str(payslip.hours_worked),
                "gross_pay": str(payslip.gross_pay),
                "currency": payslip.currency,
                "created": created,
            }
        )
        if want_pdf and len(staff_qs) == 1:
            filename = f"payslip_{staff.first_name or 'staff'}_{period_start:%Y%m}.pdf".replace(" ", "_")
            pdf_response = HttpResponse(pdf_bytes, content_type="application/pdf")
            pdf_response["Content-Disposition"] = f'attachment; filename="{filename}"'
            if request.data.get("return_base64"):
                return Response(
                    {
                        "success": True,
                        "payslip_id": str(payslip.id),
                        "pdf_base64": base64.b64encode(pdf_bytes).decode("ascii"),
                        "message_for_user": (
                            f"✓ Payslip generated for {staff.get_full_name()} "
                            f"({period_start:%b %Y}) — {payslip.gross_pay} {payslip.currency} gross."
                        ),
                    }
                )

    if not generated:
        return Response(
            {
                "success": False,
                "message_for_user": "No staff found for that period. Check the name or try all staff.",
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    if pdf_response is not None:
        return pdf_response

    return Response(
        {
            "success": True,
            "count": len(generated),
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "payslips": generated,
            "message_for_user": (
                f"✓ Generated {len(generated)} payslip(s) for "
                f"{period_start.strftime('%b %Y')} from clock hours and staff rates."
            ),
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET", "POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_compliance_reminders(request):
    """
    GET  /api/payroll/agent/compliance-reminders/list/
    POST /api/payroll/agent/compliance-reminders/seed/  — seed Morocco CNSS/tax calendar nudges
    """
    from scheduling.views_agent import _resolve_restaurant_for_agent

    restaurant, _, err = _resolve_restaurant_for_agent(request)
    if err:
        return Response({"success": False, "error": err["error"]}, status=err["status"])

    if request.method == "POST" or request.path.endswith("/seed/"):
        created_rows = []
        for tpl in morocco_compliance_templates():
            ext = tpl["external_id"]
            if ComplianceReminder.objects.filter(restaurant=restaurant, external_id=ext).exists():
                continue
            row = ComplianceReminder.objects.create(
                restaurant=restaurant,
                code=tpl["code"],
                title=tpl["title"],
                description=tpl["description"],
                category=tpl["category"],
                due_date=tpl["due_date"],
                remind_days_before=tpl["remind_days_before"],
                external_id=ext,
            )
            created_rows.append({"id": str(row.id), "title": row.title, "due_date": row.due_date.isoformat()})
        return Response(
            {
                "success": True,
                "created": len(created_rows),
                "reminders": created_rows,
                "message_for_user": (
                    f"✓ Added {len(created_rows)} compliance calendar reminder(s) "
                    "(CNSS / tax / payroll close — reminders only, no filing)."
                ),
            }
        )

    qs = ComplianceReminder.objects.filter(restaurant=restaurant).exclude(status=ComplianceReminder.STATUS_DONE)
    rows = [
        {
            "id": str(r.id),
            "title": r.title,
            "category": r.category,
            "due_date": r.due_date.isoformat(),
            "status": r.status,
            "description": r.description,
        }
        for r in qs.order_by("due_date")[:20]
    ]
    return Response({"success": True, "count": len(rows), "reminders": rows})


@api_view(["GET", "POST", "PATCH"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_compliance_documents(request):
    """
    Restaurant compliance docs with expiry (insurance, hygiene, extinguishers, registration).

    GET  /api/payroll/agent/compliance-documents/?expiring_within_days=60
    POST /api/payroll/agent/compliance-documents/  — create or seed
         body: { action: "seed" } OR { title, document_type, expires_at, ... }
    PATCH /api/payroll/agent/compliance-documents/ — update by id
         body: { id, expires_at?, title?, ... }
    """
    from scheduling.views_agent import _resolve_restaurant_for_agent
    from payroll.models import ComplianceDocument
    from payroll.services.compliance_documents import (
        DOCUMENT_TYPE_IDS,
        documents_needing_attention,
        seed_starter_documents,
        serialize_document,
    )

    restaurant, acting_user, err = _resolve_restaurant_for_agent(request)
    if err:
        return Response({"success": False, "error": err["error"]}, status=err["status"])

    data = request.data if isinstance(getattr(request, "data", None), dict) else {}

    if request.method == "GET":
        try:
            within = int(
                request.query_params.get("expiring_within_days")
                or data.get("expiring_within_days")
                or 90
            )
        except (TypeError, ValueError):
            within = 90
        within = max(0, min(365, within))
        attention_only = str(
            request.query_params.get("attention_only") or data.get("attention_only") or ""
        ).lower() in ("1", "true", "yes")
        if attention_only or within < 365:
            docs = documents_needing_attention(restaurant, within_days=within)
        else:
            docs = list(
                ComplianceDocument.objects.filter(
                    restaurant=restaurant,
                    status=ComplianceDocument.STATUS_ACTIVE,
                ).order_by("expires_at", "title")[:40]
            )
        rows = [serialize_document(d) for d in docs[:40]]
        expired = sum(1 for r in rows if r["urgency"] == "expired")
        soon = sum(1 for r in rows if r["urgency"] in ("critical", "soon"))
        unset = sum(1 for r in rows if r["urgency"] == "unset")
        return Response(
            {
                "success": True,
                "count": len(rows),
                "expired": expired,
                "expiring_soon": soon,
                "missing_date": unset,
                "documents": rows,
                "message_for_user": (
                    f"{len(rows)} compliance document(s)"
                    + (f" — {expired} expired" if expired else "")
                    + (f", {soon} due soon" if soon else "")
                    + (f", {unset} need an expiry date" if unset else "")
                    + "."
                ),
            }
        )

    action = str(_get_first(data, "action") or "").strip().lower()
    if action == "seed" or request.path.rstrip("/").endswith("/seed"):
        created = seed_starter_documents(restaurant)
        return Response(
            {
                "success": True,
                "created": len(created),
                "documents": [serialize_document(d) for d in created],
                "message_for_user": (
                    f"✓ Added {len(created)} suggested compliance document(s). "
                    "Tell me the expiry dates and I'll remind you before they lapse."
                    if created
                    else "Suggested compliance documents are already set up. "
                    "Share an expiry date to start reminders."
                ),
            }
        )

    if request.method == "PATCH" or action == "update":
        doc_id = str(_get_first(data, "id", "document_id") or "").strip()
        if not doc_id:
            return Response(
                {"success": False, "error": "id is required", "message_for_user": "Which document should I update?"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        doc = ComplianceDocument.objects.filter(restaurant=restaurant, id=doc_id).first()
        if not doc:
            return Response(
                {"success": False, "error": "Not found", "message_for_user": "I couldn't find that document."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if "title" in data and str(data.get("title") or "").strip():
            doc.title = str(data["title"]).strip()[:255]
        if "description" in data:
            doc.description = str(data.get("description") or "")[:2000]
        if "reference_number" in data:
            doc.reference_number = str(data.get("reference_number") or "")[:128]
        if "document_type" in data:
            dtype = str(data.get("document_type") or "").upper()
            if dtype in DOCUMENT_TYPE_IDS:
                doc.document_type = dtype
        if "expires_at" in data or "expiry_date" in data or "due_date" in data:
            raw = _get_first(data, "expires_at", "expiry_date", "due_date")
            parsed = parse_date(str(raw)) if raw else None
            doc.expires_at = parsed
            if parsed and parsed >= timezone.now().date():
                doc.status = ComplianceDocument.STATUS_ACTIVE
                doc.last_notified_at = None
        if "remind_days_before" in data and data.get("remind_days_before") not in (None, ""):
            try:
                doc.remind_days_before = max(1, min(365, int(data["remind_days_before"])))
            except (TypeError, ValueError):
                pass
        if str(data.get("status") or "").upper() == "ARCHIVED":
            doc.status = ComplianceDocument.STATUS_ARCHIVED
        doc.save()
        return Response(
            {
                "success": True,
                "document": serialize_document(doc),
                "message_for_user": (
                    f"✓ Updated *{doc.title}*"
                    + (
                        f" — expires {doc.expires_at.isoformat()}. I'll remind you beforehand."
                        if doc.expires_at
                        else "."
                    )
                ),
            }
        )

    # Create
    title = str(_get_first(data, "title", "name") or "").strip()
    if not title:
        return Response(
            {
                "success": False,
                "error": "title required",
                "message_for_user": "What should I call this document? (e.g. Business insurance)",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    dtype = str(_get_first(data, "document_type", "type") or "OTHER").upper()
    if dtype not in DOCUMENT_TYPE_IDS:
        # Heuristic from title
        low = title.lower()
        if "insur" in low or "assurance" in low:
            dtype = "INSURANCE"
        elif "hygien" in low or "food safety" in low or "haccp" in low:
            dtype = "HYGIENE"
        elif "extinguish" in low or "fire" in low:
            dtype = "FIRE_EXTINGUISHER"
        elif "regist" in low or "patente" in low or "licence" in low or "license" in low:
            dtype = "BUSINESS_REGISTRATION"
        else:
            dtype = "OTHER"
    expires_raw = _get_first(data, "expires_at", "expiry_date", "due_date")
    expires_at = parse_date(str(expires_raw)) if expires_raw else None
    try:
        remind_days = int(_get_first(data, "remind_days_before", "remind_days") or 30)
        remind_days = max(1, min(365, remind_days))
    except (TypeError, ValueError):
        remind_days = 30
    doc = ComplianceDocument.objects.create(
        restaurant=restaurant,
        title=title[:255],
        document_type=dtype,
        description=str(_get_first(data, "description", "notes") or "")[:2000],
        reference_number=str(_get_first(data, "reference_number", "reference") or "")[:128],
        expires_at=expires_at,
        remind_days_before=remind_days,
        created_by=acting_user,
    )
    return Response(
        {
            "success": True,
            "document": serialize_document(doc),
            "message_for_user": (
                f"✓ Tracking *{doc.title}*"
                + (
                    f" — expires {doc.expires_at.isoformat()}. "
                    f"I'll remind you about {doc.remind_days_before} days before."
                    if doc.expires_at
                    else ". Add an expiry date whenever you have it and I'll remind you."
                )
            ),
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_log_temperature(request):
    """
    POST /api/payroll/agent/temperature-log/

    Body: equipment, value_c / temperature, optional notes, min_c, max_c
    """
    from scheduling.views_agent import _resolve_restaurant_for_agent

    restaurant, acting_user, err = _resolve_restaurant_for_agent(request)
    if err:
        return Response({"success": False, "error": err["error"]}, status=err["status"])

    data = request.data if isinstance(getattr(request, "data", None), dict) else {}
    equipment = str(_get_first(data, "equipment", "location", "unit") or "").strip()
    raw_temp = _get_first(data, "value_c", "valueC", "temperature", "temp")
    notes = str(_get_first(data, "notes", "note") or "").strip()

    if not equipment or raw_temp in (None, ""):
        text = str(_get_first(data, "text", "message") or "").strip()
        parsed = _parse_temperature_message(text)
        if parsed:
            equipment, raw_temp = parsed["equipment"], parsed["value_c"]
            notes = notes or text

    if not equipment or raw_temp in (None, ""):
        return Response(
            {
                "success": False,
                "message_for_user": "Tell me the equipment and temperature, e.g. 'walk-in cooler 4°C'.",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        value_c = Decimal(str(raw_temp).replace(",", ".").replace("°", "").replace("c", "").strip())
    except (InvalidOperation, ValueError):
        return Response(
            {"success": False, "message_for_user": "I couldn't read that temperature as a number."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    min_c = _get_first(data, "min_c", "minC")
    max_c = _get_first(data, "max_c", "maxC")
    try:
        min_c = Decimal(str(min_c)) if min_c is not None else Decimal("0")
        max_c = Decimal(str(max_c)) if max_c is not None else Decimal("5")
    except (InvalidOperation, ValueError):
        min_c, max_c = Decimal("0"), Decimal("5")

    out_of_range = value_c < min_c or value_c > max_c
    reading = TemperatureReading.objects.create(
        restaurant=restaurant,
        equipment=equipment[:120],
        value_c=value_c,
        recorded_by=acting_user,
        source=TemperatureReading.SOURCE_WHATSAPP,
        notes=notes[:500],
        is_out_of_range=out_of_range,
    )

    msg = (
        f"✓ Logged {equipment}: {value_c}°C (#{_short_ref(reading.id)})."
        + (" ⚠️ Out of acceptable range — flagged for review." if out_of_range else "")
    )
    return Response(
        {
            "success": True,
            "record_id": str(reading.id),
            "is_out_of_range": out_of_range,
            "message_for_user": msg,
        },
        status=status.HTTP_201_CREATED,
    )


def _parse_temperature_message(text: str) -> dict | None:
    if not text or len(text) < 3:
        return None
    m = re.search(
        r"(.+?)\s+(-?\d+(?:[.,]\d+)?)\s*(?:°?\s*[cC]|degrees?\s*c|degr[eé]s?\s*c)",
        text,
        re.IGNORECASE,
    )
    if not m:
        m = re.search(r"(-?\d+(?:[.,]\d+)?)\s*(?:°?\s*[cC]).*?(walk[- ]?in|fridge|freezer|cooler|chambre\s+froide|cong[eé]lateur)", text, re.I)
        if m:
            return {"value_c": m.group(1).replace(",", "."), "equipment": m.group(2).strip()}
        return None
    return {"equipment": m.group(1).strip(), "value_c": m.group(2).replace(",", ".")}


@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_sync_delivery_menu(request):
    """POST /api/payroll/agent/delivery-menu/sync/ — Glovo-ready menu export."""
    from scheduling.views_agent import _resolve_restaurant_for_agent

    restaurant, _, err = _resolve_restaurant_for_agent(request)
    if err:
        return Response({"success": False, "error": err["error"]}, status=err["status"])

    data = request.data if isinstance(getattr(request, "data", None), dict) else {}
    provider = str(_get_first(data, "provider") or "GLOVO").upper()
    result = sync_delivery_menu(restaurant, provider=provider)
    return Response(result, status=status.HTTP_200_OK if result.get("success") else status.HTTP_400_BAD_REQUEST)
