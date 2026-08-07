"""Phase 11 Wave 1 — canonical handlers for legacy HTTP mutations."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.utils import timezone

from miya.services.ops.agent_bridge import dispatch_agent_post, enrich_agent_payload, run_verified_agent_mutation
from miya.services.ops.context import OpsContext, guard_entity_location, require_permission, require_restaurant
from miya.services.ops.result import OpsResult, clarify, fail, ok


def _msg(body: dict[str, Any], fallback: str = "") -> str:
    return str(body.get("message_for_user") or body.get("message") or fallback or "Done.")


# ── STAFF / TIME CLOCK ─────────────────────────────────────────────────────


def staff_clock_in(ctx: OpsContext, **args: Any) -> OpsResult:
    payload = enrich_agent_payload(ctx, args)

    def verify(_ctx: OpsContext, body: dict, _payload: dict) -> OpsResult:
        from timeclock.models import ClockEvent

        event_id = body.get("clock_event_id")
        staff_id = body.get("staff_id") or payload.get("staff_id") or ctx.user_id
        if event_id:
            ev = ClockEvent.objects.filter(id=event_id, staff_id=staff_id, event_type="in").first()
            if ev:
                return ok(
                    message=_msg(body, "Clock-in recorded."),
                    verified=True,
                    data={"clock_event_id": str(ev.id), "staff_id": str(staff_id), "operation": "staff_clock_in"},
                )
        if body.get("already_clocked_in"):
            ev = (
                ClockEvent.objects.filter(staff_id=staff_id, event_type="in")
                .order_by("-timestamp")
                .first()
            )
            if ev:
                return ok(
                    message=_msg(body),
                    verified=True,
                    data={
                        "clock_event_id": str(ev.id),
                        "staff_id": str(staff_id),
                        "already_clocked_in": True,
                        "operation": "staff_clock_in",
                    },
                )
        return fail(code="verify_failed", message="Clock-in could not be verified in the database.")

    return run_verified_agent_mutation(
        ctx,
        tool="staff_clock_in",
        path="/api/timeclock/agent/clock-in-by-phone/",
        payload=payload,
        verify=verify,
    )


def staff_clock_out(ctx: OpsContext, **args: Any) -> OpsResult:
    payload = enrich_agent_payload(ctx, args)

    def verify(_ctx: OpsContext, body: dict, _payload: dict) -> OpsResult:
        from timeclock.models import ClockEvent

        staff_id = body.get("staff_id") or payload.get("staff_id") or ctx.user_id
        event_id = body.get("clock_event_id")
        if event_id:
            ev = ClockEvent.objects.filter(
                id=event_id, staff_id=staff_id, event_type="out"
            ).first()
            if ev:
                return ok(
                    message=_msg(body, "Clock-out recorded."),
                    verified=True,
                    data={
                        "clock_event_id": str(ev.id),
                        "staff_id": str(staff_id),
                        "operation": "staff_clock_out",
                    },
                )
        return fail(code="verify_failed", message="Clock-out could not be verified.")

    return run_verified_agent_mutation(
        ctx,
        tool="staff_clock_out",
        path="/api/timeclock/agent/clock-out-by-phone/",
        payload=payload,
        verify=verify,
    )


def staff_request(ctx: OpsContext, **args: Any) -> OpsResult:
    payload = enrich_agent_payload(ctx, args)
    external_id = str(payload.get("external_id") or payload.get("inquiryId") or "").strip()
    if external_id:
        from staff.models import StaffRequest

        existing = StaffRequest.objects.filter(
            restaurant_id=ctx.restaurant_id, external_id=external_id
        ).first()
        if existing:
            return ok(
                message="That request was already logged.",
                verified=True,
                code="duplicate_suppressed",
                data={"request_id": str(existing.id), "deduplicated": True, "operation": "staff_request"},
            )

    def verify(_ctx: OpsContext, body: dict, _payload: dict) -> OpsResult:
        from staff.models import StaffRequest

        req_id = body.get("id") or body.get("request_id") or body.get("staff_request_id")
        if not req_id:
            return fail(code="verify_failed", message="Staff request id missing from agent response.")
        req = StaffRequest.objects.filter(id=req_id, restaurant_id=ctx.restaurant_id).first()
        if not req or req.status not in ("PENDING", "ESCALATED"):
            return fail(code="verify_failed", message="Staff request could not be verified.")
        return ok(
            message=_msg(body, "Request logged."),
            verified=True,
            data={"request_id": str(req.id), "status": req.status, "operation": "staff_request"},
        )

    return run_verified_agent_mutation(
        ctx,
        tool="staff_request",
        path="/api/staff/agent/requests/ingest/",
        payload=payload,
        verify=verify,
        idempotency_key=f"staff_request:{ctx.restaurant_id}:{external_id}" if external_id else None,
    )


def approve_staff_request(ctx: OpsContext, **args: Any) -> OpsResult:
    payload = enrich_agent_payload(ctx, args)
    req_id = str(payload.get("request_id") or payload.get("requestId") or payload.get("id") or "")
    guard = _load_staff_request(ctx, req_id) if req_id else None
    if isinstance(guard, OpsResult):
        return guard

    def verify(_ctx: OpsContext, body: dict, _payload: dict) -> OpsResult:
        from staff.models import StaffRequest

        rid = body.get("request_id") or req_id
        req = StaffRequest.objects.filter(id=rid, restaurant_id=ctx.restaurant_id).first()
        if not req or req.status != "APPROVED":
            return fail(code="verify_failed", message="Approval could not be verified.")
        return ok(
            message=_msg(body, "Request approved."),
            verified=True,
            data={"request_id": str(req.id), "status": req.status, "operation": "approve_staff_request"},
        )

    return run_verified_agent_mutation(
        ctx,
        tool="approve_staff_request",
        path="/api/staff/agent/requests/approve/",
        payload=payload,
        permission="miya_full_tools",
        verify=verify,
        guard_entity=guard,
    )


def reject_staff_request(ctx: OpsContext, **args: Any) -> OpsResult:
    payload = enrich_agent_payload(ctx, args)
    req_id = str(payload.get("request_id") or payload.get("requestId") or payload.get("id") or "")
    guard = _load_staff_request(ctx, req_id) if req_id else None
    if isinstance(guard, OpsResult):
        return guard

    def verify(_ctx: OpsContext, body: dict, _payload: dict) -> OpsResult:
        from staff.models import StaffRequest

        rid = body.get("request_id") or req_id
        req = StaffRequest.objects.filter(id=rid, restaurant_id=ctx.restaurant_id).first()
        if not req or req.status != "REJECTED":
            return fail(code="verify_failed", message="Rejection could not be verified.")
        return ok(
            message=_msg(body, "Request rejected."),
            verified=True,
            data={"request_id": str(req.id), "status": req.status, "operation": "reject_staff_request"},
        )

    return run_verified_agent_mutation(
        ctx,
        tool="reject_staff_request",
        path="/api/staff/agent/requests/reject/",
        payload=payload,
        permission="miya_full_tools",
        verify=verify,
        guard_entity=guard,
    )


# ── SCHEDULING ─────────────────────────────────────────────────────────────


def request_time_off(ctx: OpsContext, **args: Any) -> OpsResult:
    payload = enrich_agent_payload(ctx, args)

    def verify(_ctx: OpsContext, body: dict, _payload: dict) -> OpsResult:
        from scheduling.models import TimeOffRequest

        tor_id = body.get("id")
        if not tor_id:
            return fail(code="verify_failed", message="Time-off request id missing.")
        tor = TimeOffRequest.objects.filter(
            id=tor_id, staff__restaurant_id=ctx.restaurant_id
        ).first()
        if not tor or tor.status != "PENDING":
            return fail(code="verify_failed", message="Time-off request could not be verified.")
        return ok(
            message=_msg(body, "Time-off request submitted."),
            verified=True,
            data={
                "time_off_id": str(tor.id),
                "status": tor.status,
                "operation": "request_time_off",
                "idempotent": bool(body.get("idempotent")),
                "manager_notified": bool(body.get("manager_notified")),
            },
        )

    return run_verified_agent_mutation(
        ctx,
        tool="request_time_off",
        path="/api/scheduling/agent/time-off/request/",
        payload=payload,
        verify=verify,
    )


def create_shift(ctx: OpsContext, **args: Any) -> OpsResult:
    from core.agent_params import enrich_create_shift_payload

    payload = enrich_agent_payload(ctx, args)
    payload = enrich_create_shift_payload(payload)

    def verify(_ctx: OpsContext, body: dict, _payload: dict) -> OpsResult:
        from scheduling.models import AssignedShift

        shift_data = body.get("shift") or {}
        shift_id = body.get("shift_id") or shift_data.get("id") or body.get("id")
        if not shift_id:
            return fail(code="verify_failed", message="Shift id missing from create response.")
        shift = AssignedShift.objects.filter(
            id=shift_id, schedule__restaurant_id=ctx.restaurant_id
        ).first()
        if not shift:
            return fail(code="verify_failed", message="Created shift could not be verified.")
        loc_err = guard_entity_location(ctx, shift)
        if loc_err:
            return loc_err
        return ok(
            message=_msg(body, "Shift created."),
            verified=True,
            data={
                "shift_id": str(shift.id),
                "status": shift.status,
                "idempotent": bool(body.get("idempotent")),
                "operation": "create_shift",
            },
        )

    return run_verified_agent_mutation(
        ctx,
        tool="create_shift",
        path="/api/scheduling/agent/create-shift/",
        payload=payload,
        permission="edit_schedule",
        verify=verify,
    )


def assign_coverage(ctx: OpsContext, **args: Any) -> OpsResult:
    perm_err = require_permission(ctx, "edit_schedule")
    if perm_err:
        return perm_err
    payload = enrich_agent_payload(ctx, args)
    shift_id = str(payload.get("shift_id") or payload.get("shiftId") or "")
    guard = _load_shift(ctx, shift_id) if shift_id else None
    if isinstance(guard, OpsResult):
        return guard

    def verify(_ctx: OpsContext, body: dict, pl: dict) -> OpsResult:
        from scheduling.models import AssignedShift

        sid = body.get("shift_id") or shift_id
        staff_id = pl.get("staff_id") or pl.get("staffId")
        shift = AssignedShift.objects.filter(id=sid, schedule__restaurant_id=ctx.restaurant_id).first()
        if not shift or str(shift.staff_id) != str(staff_id) or shift.status != "CONFIRMED":
            return fail(code="verify_failed", message="Coverage assignment could not be verified.")
        return ok(
            message=_msg(body, "Coverage assigned."),
            verified=True,
            data={
                "shift_id": str(shift.id),
                "staff_id": str(shift.staff_id),
                "operation": "assign_coverage",
                "idempotent": bool(body.get("idempotent")),
                "notification_sent": bool(body.get("notification_sent")),
            },
        )

    return run_verified_agent_mutation(
        ctx,
        tool="assign_coverage",
        path="/api/scheduling/agent/assign-coverage/",
        payload=payload,
        permission="edit_schedule",
        verify=verify,
        guard_entity=guard,
    )


def mark_no_show(ctx: OpsContext, **args: Any) -> OpsResult:
    payload = enrich_agent_payload(ctx, args)
    shift_id = str(payload.get("shift_id") or payload.get("shiftId") or "")
    guard = _load_shift(ctx, shift_id) if shift_id else None
    if isinstance(guard, OpsResult):
        return guard

    def verify(_ctx: OpsContext, body: dict, _payload: dict) -> OpsResult:
        from scheduling.models import AssignedShift

        sid = body.get("shift_id") or shift_id
        shift = AssignedShift.objects.filter(id=sid, schedule__restaurant_id=ctx.restaurant_id).first()
        if not shift or shift.status != "NO_SHOW":
            return fail(code="verify_failed", message="No-show status could not be verified.")
        return ok(
            message=_msg(body, "Shift marked no-show."),
            verified=True,
            data={"shift_id": str(shift.id), "status": shift.status, "operation": "mark_no_show"},
        )

    return run_verified_agent_mutation(
        ctx,
        tool="mark_no_show",
        path="/api/scheduling/agent/mark-no-show/",
        payload=payload,
        permission="edit_schedule",
        verify=verify,
        guard_entity=guard,
    )


# ── FINANCE ────────────────────────────────────────────────────────────────


def assign_invoice(ctx: OpsContext, **args: Any) -> OpsResult:
    payload = enrich_agent_payload(ctx, args)

    def verify(_ctx: OpsContext, body: dict, pl: dict) -> OpsResult:
        from finance.models import Invoice

        invoice_ids = body.get("invoice_ids") or pl.get("invoice_ids") or []
        assignee = body.get("assignee") or {}
        assignee_id = assignee.get("id") or pl.get("assignee_id")
        if not invoice_ids or not assignee_id:
            return fail(code="verify_failed", message="Invoice assignment response incomplete.")
        for inv_id in invoice_ids:
            inv = Invoice.objects.filter(id=inv_id, restaurant_id=ctx.restaurant_id).first()
            if not inv or str(inv.assigned_to_id) != str(assignee_id):
                return fail(code="verify_failed", message="Invoice assignee could not be verified.")
            loc_err = guard_entity_location(ctx, inv)
            if loc_err:
                return loc_err
        return ok(
            message=_msg(body, "Invoice(s) assigned."),
            verified=True,
            data={
                "invoice_ids": [str(i) for i in invoice_ids],
                "assignee_id": str(assignee_id),
                "count": len(invoice_ids),
                "operation": "assign_invoice",
            },
        )

    return run_verified_agent_mutation(
        ctx,
        tool="assign_invoice",
        path="/api/finance/agent/invoices/assign/",
        payload=payload,
        permission="run_reports",
        verify=verify,
    )


# ── COMMUNICATION / OPERATIONS ─────────────────────────────────────────────


def send_announcement(ctx: OpsContext, **args: Any) -> OpsResult:
    err = require_restaurant(ctx) or require_permission(ctx, "miya_full_tools")
    if err:
        return err

    payload = enrich_agent_payload(ctx, args)
    message = str(payload.get("message") or "").strip()
    if not message:
        return fail(code="message_required", message="What should the announcement say?")

    title = str(payload.get("title") or "Announcement").strip() or "Announcement"
    audience = payload.get("audience")
    sender = ctx.user if getattr(ctx.user, "pk", None) else None

    staff_ids = roles = departments = tags = None
    broadcast_all = payload.get("broadcast_all") is True or audience == "all"
    if isinstance(audience, dict):
        staff_ids = audience.get("staff_ids")
        roles = audience.get("roles")
        departments = audience.get("departments")
        tags = audience.get("tags")
        if audience.get("all") is True:
            broadcast_all = True

    from notifications.models import Notification
    from notifications.services import notification_service

    before = Notification.objects.filter(
        recipient__restaurant_id=ctx.restaurant_id,
        notification_type="ANNOUNCEMENT",
        created_at__gte=timezone.now() - timedelta(minutes=2),
    ).count()

    success, count, err_msg, details = notification_service.send_announcement_to_audience(
        restaurant_id=str(ctx.restaurant_id),
        title=title,
        message=message,
        sender=sender,
        staff_ids=staff_ids,
        roles=roles,
        departments=departments,
        tags=tags,
        channels=["app", "whatsapp"],
        broadcast_all=broadcast_all,
    )
    if not success:
        return fail(code="announcement_failed", message=err_msg or "Announcement could not be sent.")

    after = Notification.objects.filter(
        recipient__restaurant_id=ctx.restaurant_id,
        notification_type="ANNOUNCEMENT",
        created_at__gte=timezone.now() - timedelta(minutes=2),
    ).count()
    created = after - before
    if created <= 0 and count <= 0:
        return fail(code="verify_failed", message="Announcement notifications could not be verified.")

    return ok(
        message=f"Announcement sent to {count} recipient(s).",
        verified=True,
        data={
            "notification_count": count,
            "notifications_created": created,
            "whatsapp_sent": (details or {}).get("whatsapp_sent", count),
            "operation": "send_announcement",
        },
    )


def notify_manager_urgent(ctx: OpsContext, **args: Any) -> OpsResult:
    err = require_restaurant(ctx) or require_permission(ctx, "manage_widgets")
    if err:
        return err

    payload = enrich_agent_payload(ctx, args)
    message = payload.get("message")
    task_id = payload.get("task_id") or payload.get("taskId")

    from dashboard.api.operations_live import notify_managers_urgent

    result = notify_managers_urgent(
        ctx.restaurant,
        message=str(message).strip() if message else None,
        task_id=str(task_id) if task_id else None,
    )
    app_sent = int(result.get("managers_app") or 0)
    wa_sent = int(result.get("managers_whatsapp") or 0)
    if app_sent + wa_sent <= 0:
        return fail(
            code="verify_failed",
            message="No managers could be alerted — verify manager accounts exist.",
            data={"operation": "notify_manager_urgent", **result},
        )
    return ok(
        message=str(result.get("message_for_user") or "Managers alerted."),
        verified=True,
        data={"operation": "notify_manager_urgent", **result},
    )


def chase_operational_record(ctx: OpsContext, **args: Any) -> OpsResult:
    payload = enrich_agent_payload(ctx, args)
    record, record_type, guard_err = _resolve_operational_record(ctx, payload)
    if guard_err:
        return guard_err

    before_count = getattr(record, "follow_up_count", 0) or 0

    def verify(_ctx: OpsContext, body: dict, _payload: dict) -> OpsResult:
        record.refresh_from_db()
        if record.follow_up_count <= before_count:
            return fail(code="verify_failed", message="Follow-up was not recorded.")
        return ok(
            message=_msg(body, "Follow-up sent."),
            verified=True,
            data={
                "record_id": str(record.id),
                "record_type": record_type,
                "follow_up_count": record.follow_up_count,
                "operation": "chase_operational_record",
            },
        )

    return run_verified_agent_mutation(
        ctx,
        tool="chase_operational_record",
        path="/api/staff/agent/records/chase/",
        payload=payload,
        permission="manage_widgets",
        verify=verify,
        guard_entity=record,
    )


# ── INVENTORY / COMPLIANCE ─────────────────────────────────────────────────


def report_waste(ctx: OpsContext, **args: Any) -> OpsResult:
    payload = enrich_agent_payload(ctx, args)

    def verify(_ctx: OpsContext, body: dict, pl: dict) -> OpsResult:
        from inventory.models import WasteEntry

        waste_id = body.get("waste_id")
        if not waste_id:
            return fail(code="verify_failed", message="Waste entry id missing.")
        entry = WasteEntry.objects.filter(id=waste_id, restaurant_id=ctx.restaurant_id).first()
        if not entry:
            return fail(code="verify_failed", message="Waste entry could not be verified.")
        qty = Decimal(str(pl.get("quantity") or 0))
        if qty > 0 and entry.quantity != qty:
            return fail(code="verify_failed", message="Waste quantity mismatch on verify.")
        return ok(
            message=_msg(body, "Waste recorded."),
            verified=True,
            data={"waste_id": str(entry.id), "operation": "report_waste"},
        )

    return run_verified_agent_mutation(
        ctx,
        tool="report_waste",
        path="/api/inventory/agent/waste/",
        payload=payload,
        permission="edit_inventory",
        verify=verify,
    )


def update_compliance_document(ctx: OpsContext, **args: Any) -> OpsResult:
    payload = enrich_agent_payload(ctx, args)
    doc_id = str(payload.get("id") or payload.get("document_id") or "").strip()
    guard = _load_compliance_doc(ctx, doc_id) if doc_id else None
    if isinstance(guard, OpsResult):
        return guard

    expected: dict[str, Any] = {}
    if "expires_at" in payload or "expiry_date" in payload or "due_date" in payload:
        expected["expires_at"] = payload.get("expires_at") or payload.get("expiry_date") or payload.get("due_date")
    if payload.get("title"):
        expected["title"] = str(payload.get("title")).strip()[:255]

    def verify(_ctx: OpsContext, body: dict, _payload: dict) -> OpsResult:
        from payroll.models import ComplianceDocument

        did = (body.get("document") or {}).get("id") or doc_id
        doc = ComplianceDocument.objects.filter(id=did, restaurant_id=ctx.restaurant_id).first()
        if not doc:
            return fail(code="verify_failed", message="Compliance document could not be verified.")
        if expected.get("title") and doc.title != expected["title"]:
            return fail(code="verify_failed", message="Document title mismatch on verify.")
        return ok(
            message=_msg(body, "Document updated."),
            verified=True,
            data={"document_id": str(doc.id), "document": body.get("document"), "operation": "update_compliance_document"},
        )

    return run_verified_agent_mutation(
        ctx,
        tool="update_compliance_document",
        path="/api/payroll/agent/compliance-documents/",
        payload=payload,
        permission="manage_compliance_docs",
        verify=verify,
        guard_entity=guard,
        method="PATCH",
    )


def recognize_staff(ctx: OpsContext, **args: Any) -> OpsResult:
    payload = enrich_agent_payload(ctx, args)
    title = str(payload.get("title") or payload.get("message") or "").strip()
    if not title:
        return clarify(message="What recognition title should I use?")
    payload["title"] = title

    def verify(_ctx: OpsContext, body: dict, _payload: dict) -> OpsResult:
        from staff.models_task import SafetyRecognition

        rec_id = body.get("recognition_id")
        if not rec_id:
            return fail(code="verify_failed", message="Recognition id missing.")
        rec = SafetyRecognition.objects.filter(id=rec_id, restaurant_id=ctx.restaurant_id).first()
        if not rec or rec.title != title:
            return fail(code="verify_failed", message="Recognition could not be verified.")
        return ok(
            message=_msg(body, "Recognition recorded."),
            verified=True,
            data={
                "recognition_id": str(rec.id),
                "staff_id": str(rec.staff_id),
                "title": rec.title,
                "operation": "recognize_staff",
            },
        )

    return run_verified_agent_mutation(
        ctx,
        tool="recognize_staff",
        path="/api/agent/recognize-staff/",
        payload=payload,
        permission="miya_full_tools",
        verify=verify,
    )


# ── Helpers ────────────────────────────────────────────────────────────────


def _load_staff_request(ctx: OpsContext, req_id: str):
    from staff.models import StaffRequest

    if not req_id:
        return fail(code="request_id_required", message="Which staff request should I update?")
    req = StaffRequest.objects.filter(id=req_id, restaurant_id=ctx.restaurant_id).first()
    if not req:
        return fail(code="not_found", message="Staff request not found.")
    return req


def _load_shift(ctx: OpsContext, shift_id: str):
    from scheduling.models import AssignedShift

    if not shift_id:
        return fail(code="shift_id_required", message="Which shift?")
    shift = AssignedShift.objects.filter(id=shift_id, schedule__restaurant_id=ctx.restaurant_id).first()
    if not shift:
        return fail(code="not_found", message="Shift not found.")
    return shift


def _load_compliance_doc(ctx: OpsContext, doc_id: str):
    from payroll.models import ComplianceDocument

    if not doc_id:
        return fail(code="document_id_required", message="Which compliance document?")
    doc = ComplianceDocument.objects.filter(id=doc_id, restaurant_id=ctx.restaurant_id).first()
    if not doc:
        return fail(code="not_found", message="Compliance document not found.")
    return doc


def _resolve_operational_record(ctx: OpsContext, payload: dict):
    from staff.views_agent import _find_operational_record

    record_type, record = _find_operational_record(
        ctx.restaurant,
        record_id=payload.get("record_id") or payload.get("recordId") or payload.get("id"),
        record_type=payload.get("record_type") or payload.get("recordType") or payload.get("type"),
        query=payload.get("q") or payload.get("query"),
    )
    if not record:
        return None, "", fail(code="not_found", message="Operational record not found.")
    loc_err = guard_entity_location(ctx, record)
    if loc_err:
        return None, "", loc_err
    return record, record_type, None
