"""
Structured Action / Tool Layer + Action Execution Layer.

Mutations execute only through these structured actions — never from NL text.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from miya.services.intelligence.audit import record_audit, timed_call
from miya.services.intelligence.events import emit_ops_event
from miya.services.intelligence.idempotency import claim_operation_once, ensure_operation_id
from miya.services.ops.context import OpsContext
from miya.services.ops.result import OpsResult, fail, ok
from miya.services.intelligence.verify import require_verified

logger = logging.getLogger("miya.intelligence.actions")

# Canonical Phase-1 action names → underlying tool/service aliases
ACTION_CATALOG: dict[str, dict[str, Any]] = {
    # Reality (read)
    "get_current_task": {"mutates": False, "aliases": ["get_dashboard_task"]},
    "get_current_incident": {"mutates": False, "aliases": ["get_incident"]},
    "get_current_staff": {"mutates": False, "aliases": ["find_staff", "staff_lookup"]},
    "get_current_establishment": {"mutates": False, "aliases": ["find_establishments"]},
    "get_current_assignment": {
        "mutates": False,
        "aliases": ["find_category_owners", "find_responsible_people"],
    },
    "get_current_document": {"mutates": False, "aliases": ["get_document", "show_document"]},
    "get_current_invoice": {"mutates": False, "aliases": ["get_invoice"]},
    "get_current_reminder": {"mutates": False, "aliases": ["list_reminders"]},
    "get_current_meeting": {"mutates": False, "aliases": ["list_meetings", "list_calendar_events"]},
    "recall_operational_memory": {"mutates": False, "aliases": []},
    "get_event_history": {"mutates": False, "aliases": []},
    "operational_search": {"mutates": False, "aliases": ["ops_search", "semantic_search"]},
    # Tasks
    "create_task": {"mutates": True, "aliases": ["create_dashboard_task", "create_ops_task"]},
    "assign_task": {"mutates": True, "aliases": ["reassign_dashboard_task", "assign_ops_task"]},
    "update_task_status": {
        "mutates": True,
        "aliases": ["update_dashboard_task_status", "update_ops_task_status"],
    },
    "update_task": {"mutates": True, "aliases": ["update_dashboard_task"]},
    "complete_task": {"mutates": True, "aliases": []},
    # Incidents
    "create_incident": {"mutates": True, "aliases": ["report_incident"]},
    "assign_incident": {"mutates": True, "aliases": ["route_incident"]},
    "resolve_incident": {"mutates": True, "aliases": ["close_incident"]},
    "attach_incident_photo": {"mutates": True, "aliases": []},
    "record_invoice": {"mutates": True, "aliases": []},
    "sync_compliance_reminder": {"mutates": True, "aliases": []},
    "find_staff": {"mutates": False, "aliases": ["staff_lookup"]},
    # Responsibility
    "assign_category": {"mutates": True, "aliases": ["assign_responsibility"]},
    "update_responsibility": {"mutates": True, "aliases": ["assign_responsibility"]},
    # Documents
    "retrieve_document": {"mutates": False, "aliases": ["get_document", "show_document", "find_documents"]},
    # Invoices
    "submit_invoice": {"mutates": True, "aliases": ["request_invoice_approval"]},
    "approve_invoice": {"mutates": True, "aliases": []},
    "reject_invoice": {"mutates": True, "aliases": []},
    "mark_invoice_paid": {"mutates": True, "aliases": []},
    # Reminders / meetings
    "create_reminder": {"mutates": True, "aliases": ["create_personal_reminder"]},
    "create_meeting": {"mutates": True, "aliases": ["create_calendar_event"]},
    # Phase 11 Wave 1
    "clock_in": {"mutates": True, "aliases": ["staff_clock_in"]},
    "clock_out": {"mutates": True, "aliases": ["staff_clock_out"]},
    "submit_staff_request": {"mutates": True, "aliases": ["staff_request"]},
    "approve_staff_request": {"mutates": True, "aliases": []},
    "reject_staff_request": {"mutates": True, "aliases": []},
    "request_time_off": {"mutates": True, "aliases": []},
    "create_shift": {"mutates": True, "aliases": []},
    "assign_coverage": {"mutates": True, "aliases": []},
    "mark_no_show": {"mutates": True, "aliases": []},
    "assign_invoice": {"mutates": True, "aliases": []},
    "send_announcement": {"mutates": True, "aliases": []},
    "notify_manager_urgent": {"mutates": True, "aliases": []},
    "chase_operational_record": {"mutates": True, "aliases": []},
    "report_waste": {"mutates": True, "aliases": []},
    "update_compliance_document": {"mutates": True, "aliases": []},
    "recognize_staff": {"mutates": True, "aliases": []},
}

_ALIAS_TO_ACTION: dict[str, str] = {}
for _action, _meta in ACTION_CATALOG.items():
    _ALIAS_TO_ACTION[_action] = _action
    for _alias in _meta.get("aliases") or []:
        _ALIAS_TO_ACTION[_alias] = _action


def is_structured_action(name: str) -> bool:
    return (name or "").strip() in _ALIAS_TO_ACTION


def resolve_action_name(name: str) -> str:
    return _ALIAS_TO_ACTION.get((name or "").strip(), (name or "").strip())


def execute_structured_action(
    name: str,
    arguments: dict[str, Any] | None,
    *,
    ctx: OpsContext,
    execution_context: dict[str, Any] | None = None,
    intent: str = "",
) -> OpsResult:
    """
    Execute a structured action through Mizan services with:
      operation_id → idempotency → service → verify envelope → event → audit
    """
    action = resolve_action_name(name)
    args = dict(arguments or {})
    exec_ctx = dict(execution_context or {})
    message_id = str(exec_ctx.get("message_id") or args.pop("_message_id", "") or "")
    provided_oid = str(args.pop("_operation_id", "") or args.pop("operation_id", "") or "")
    operation_id = ensure_operation_id(
        action, args, message_id=message_id, provided=provided_oid
    )

    mutates = bool((ACTION_CATALOG.get(action) or {}).get("mutates"))
    if mutates and not claim_operation_once(operation_id):
        result = ok(
            message="That operation was already applied (duplicate suppressed).",
            verified=True,
            code="duplicate_suppressed",
            data={
                "operation": action,
                "operation_id": operation_id,
                "deduplicated": True,
                "success": True,
            },
        )
        _finish(action, args, result, exec_ctx, operation_id, intent, 0.0)
        return result

    handler = _HANDLERS.get(action)
    if handler is None:
        return fail(
            code="unknown_action",
            message=f"Unknown structured action: {action}",
            data={"operation": action, "operation_id": operation_id},
        )

    try:
        raw, elapsed_ms = timed_call(handler, ctx, args)
    except Exception as exc:
        logger.exception("structured action %s failed", action)
        raw = fail(
            code="action_error",
            message="Something went wrong executing that action.",
            data={"operation": action, "operation_id": operation_id, "error": str(exc)[:200]},
        )
        elapsed_ms = 0.0

    result = _normalize_result(raw, action=action, operation_id=operation_id, args=args)
    _finish(action, args, result, exec_ctx, operation_id, intent, elapsed_ms)
    if result.success and result.verified and mutates and not (result.data or {}).get("deduplicated"):
        if not (result.data or {}).get("audit_emitted"):
            emit_ops_event(
                event_type=f"{action}.verified",
                operation=action,
                execution_context=exec_ctx,
                entity_type=_entity_type(action),
                entity_id=_entity_id(result),
                entity_label=_entity_label(result),
                payload={
                    "operation_id": operation_id,
                    **_entity_snapshot(result),
                    "task": (result.data or {}).get("task"),
                    "incident": (result.data or {}).get("incident"),
                    "invoice": (result.data or {}).get("invoice"),
                    "reminder": (result.data or {}).get("reminder"),
                },
                success=True,
                restaurant=ctx.restaurant,
                actor=ctx.user,
            )
    if mutates:
        result = require_verified(result)
    return result


def _finish(
    action: str,
    args: dict[str, Any],
    result: OpsResult,
    exec_ctx: dict[str, Any],
    operation_id: str,
    intent: str,
    elapsed_ms: float,
) -> None:
    body = result.as_tool_response()
    record_audit(
        message_id=str(exec_ctx.get("message_id") or ""),
        conversation_id=str(exec_ctx.get("conversation_id") or ""),
        operation_id=operation_id,
        user_id=str(exec_ctx.get("user_id") or ""),
        organization_id=str(exec_ctx.get("organization_id") or ""),
        establishment_id=str(exec_ctx.get("establishment_id") or ""),
        intent=intent or action,
        tool=action,
        arguments=args,
        result=body,
        execution_time_ms=elapsed_ms,
    )


def _normalize_result(
    raw: OpsResult,
    *,
    action: str,
    operation_id: str,
    args: dict[str, Any],
) -> OpsResult:
    data = dict(raw.data or {})
    data["operation"] = action
    data["operation_id"] = operation_id
    # Enrich common mutation fields for tool consumers
    if action == "complete_task" or (
        action == "update_task_status"
        and str(args.get("status") or "").upper() in ("COMPLETED", "DONE", "COMPLETE", "CLOSE", "CLOSED")
    ):
        task = data.get("task") or {}
        if isinstance(task, dict):
            data.setdefault("task_id", task.get("id"))
            data.setdefault("previous_status", data.get("previous_status"))
            data.setdefault("new_status", task.get("status") or "COMPLETED")
    return OpsResult(
        success=raw.success,
        code=raw.code,
        message_for_user=raw.message_for_user,
        miya_directive=raw.miya_directive,
        data=data,
        verified=raw.verified,
        needs_clarification=raw.needs_clarification,
    )


def _entity_type(action: str) -> str:
    if "task" in action:
        return "task"
    if "incident" in action:
        return "incident"
    if "invoice" in action:
        return "invoice"
    if "document" in action:
        return "document"
    if "reminder" in action:
        return "reminder"
    if "meeting" in action:
        return "meeting"
    if "staff" in action:
        return "staff"
    if "establishment" in action:
        return "establishment"
    if "categor" in action or "responsib" in action or "assignment" in action:
        return "responsibility"
    return "entity"


def _entity_id(result: OpsResult) -> str:
    data = result.data or {}
    for key in ("task", "incident", "invoice", "document", "reminder", "meeting", "establishment"):
        row = data.get(key)
        if isinstance(row, dict) and row.get("id"):
            return str(row["id"])
    for key in ("task_id", "incident_id", "invoice_id", "document_id", "reminder_id"):
        if data.get(key):
            return str(data[key])
    return ""


def _entity_label(result: OpsResult) -> str:
    data = result.data or {}
    for key in ("task", "incident", "invoice", "document", "reminder", "meeting"):
        row = data.get(key)
        if isinstance(row, dict):
            return str(row.get("title") or row.get("name") or row.get("vendor") or "")[:255]
    return ""


def _entity_snapshot(result: OpsResult) -> dict[str, Any]:
    data = result.data or {}
    out = {}
    for key in ("task_id", "incident_id", "invoice_id", "previous_status", "new_status", "status"):
        if key in data:
            out[key] = data[key]
    task = data.get("task")
    if isinstance(task, dict) and task.get("status"):
        out.setdefault("new_status", task.get("status"))
        out.setdefault("title", task.get("title"))
    return out


# ── Handlers (call existing ops services — single business logic) ──────────


def _args_str(args: dict[str, Any], *keys: str, default: str = "") -> str:
    for k in keys:
        v = args.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return default


def _handle_get_current_task(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.intelligence.reality import get_current_task

    return get_current_task(
        ctx,
        task_id=_args_str(args, "task_id", "task_ref", "id"),
        q=_args_str(args, "q", "query", "title"),
        title=_args_str(args, "title"),
        assignee_name=_args_str(args, "assignee_name", "staff_name"),
    )


def _handle_get_current_incident(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.intelligence.reality import get_current_incident

    return get_current_incident(
        ctx,
        incident_id=_args_str(args, "incident_id", "id"),
        q=_args_str(args, "q", "query"),
    )


def _handle_get_current_staff(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.intelligence.reality import get_current_staff

    return get_current_staff(
        ctx,
        name=_args_str(args, "name"),
        role=_args_str(args, "role"),
        q=_args_str(args, "q", "query"),
        limit=int(args.get("limit") or 20),
    )


def _handle_get_current_establishment(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.intelligence.reality import get_current_establishment

    return get_current_establishment(
        ctx,
        q=_args_str(args, "q", "name", "query"),
        location_id=_args_str(args, "location_id", "establishment_id", "id"),
    )


def _handle_get_current_assignment(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.intelligence.reality import get_current_assignment

    return get_current_assignment(
        ctx,
        category=_args_str(args, "category", "name"),
        q=_args_str(args, "q", "query"),
    )


def _handle_get_current_document(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.intelligence.reality import get_current_document

    return get_current_document(
        ctx,
        document_id=_args_str(args, "document_id", "id"),
        q=_args_str(args, "q", "query", "title"),
    )


def _handle_get_current_invoice(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.intelligence.reality import get_current_invoice

    return get_current_invoice(
        ctx,
        invoice_id=_args_str(args, "invoice_id", "id"),
        q=_args_str(args, "q", "vendor", "query"),
    )


def _handle_get_current_reminder(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.intelligence.reality import get_current_reminder

    return get_current_reminder(
        ctx,
        reminder_id=_args_str(args, "reminder_id", "id"),
        q=_args_str(args, "q", "query", "title"),
    )


def _handle_get_current_meeting(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.intelligence.reality import get_current_meeting

    return get_current_meeting(
        ctx,
        meeting_id=_args_str(args, "meeting_id", "id", "event_id"),
        q=_args_str(args, "q", "query", "title"),
    )


def _handle_recall_operational_memory(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.intelligence.operational_memory import recall_operational_memory

    return recall_operational_memory(
        ctx,
        q=_args_str(args, "q", "query"),
        entity_type=_args_str(args, "entity_type", "kind"),
        entity_id=_args_str(args, "entity_id", "id"),
        days=int(args.get("days") or 14),
    )


def _handle_get_event_history(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.intelligence.event_history import get_event_history

    return get_event_history(
        ctx,
        event_type=_args_str(args, "event_type"),
        entity_type=_args_str(args, "entity_type", "kind"),
        entity_id=_args_str(args, "entity_id", "id"),
        q=_args_str(args, "q", "query"),
        limit=int(args.get("limit") or 40),
    )


def _handle_operational_search(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.intelligence.search import operational_search
    from miya.services.ops.result import ok as ops_ok

    q = _args_str(args, "q", "query", "message")
    result = operational_search(
        user=ctx.user,
        query=q,
        restaurant=ctx.restaurant,
        session_context={
            "restaurant_id": ctx.restaurant_id,
            "location_id": ctx.location_id or "",
            "channel": ctx.channel,
            "user_id": ctx.user_id,
            "role": ctx.role,
        },
        channel=ctx.channel,
    )
    return ops_ok(
        message=result.reply,
        verified=True,
        data=result.to_dict(),
    )


def _handle_create_task(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.ops.tasks import create_task

    return create_task(
        ctx,
        title=_args_str(args, "title", "name") or "Task",
        assignee_name=_args_str(args, "assignee_name", "staff_name"),
        assignee_id=_args_str(args, "assignee_id"),
        description=_args_str(args, "description", "body"),
        priority=_args_str(args, "priority") or "MEDIUM",
        category=_args_str(args, "category"),
    )


def _handle_assign_task(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.ops.tasks import assign_task

    return assign_task(
        ctx,
        assignee_name=_args_str(args, "assignee_name", "staff_name", "name"),
        assignee_id=_args_str(args, "assignee_id"),
        task_id=_args_str(args, "task_id", "task_ref", "id"),
        q=_args_str(args, "q", "query"),
        title=_args_str(args, "title"),
    )


def _handle_update_task_status(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.ops.tasks import update_task_status

    if "assignee_scope" in args:
        assignee_scope = bool(args.get("assignee_scope"))
    else:
        assignee_scope = ctx.channel in ("whatsapp", "mobile")
    if "notify_managers" in args:
        notify_managers = bool(args.get("notify_managers"))
    else:
        notify_managers = ctx.channel in ("whatsapp", "mobile")

    return update_task_status(
        ctx,
        status=_args_str(args, "status", "new_status"),
        task_id=_args_str(args, "task_id", "task_ref", "id"),
        q=_args_str(args, "q", "query", "title"),
        title=_args_str(args, "title"),
        operation_id=_args_str(args, "_operation_id", "operation_id"),
        skip_idempotency=True,  # ActionExecutor already claimed operation_id
        assignee_scope=assignee_scope,
        notify_managers=notify_managers,
    )


def _handle_update_task(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.ops.tasks import update_task

    return update_task(
        ctx,
        task_id=_args_str(args, "task_id", "task_ref", "id"),
        q=_args_str(args, "q", "query", "title", "task_title"),
        title=_args_str(args, "title"),
        priority=args.get("priority"),
        due_date=args.get("due_date") or args.get("dueDate") or args.get("due") or args.get("deadline"),
        description=args.get("description") or args.get("body") or args.get("notes"),
        require_photo_proof=(
            args.get("require_photo_proof")
            if "require_photo_proof" in args
            else args.get("requirePhotoProof")
            if "requirePhotoProof" in args
            else args.get("photo_proof")
            if "photo_proof" in args
            else None
        ),
    )


def _handle_complete_task(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    merged = {**args, "status": "COMPLETED"}
    return _handle_update_task_status(ctx, merged)


def _handle_create_incident(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.ops.incidents import create_incident

    return create_incident(
        ctx,
        description=_args_str(args, "description", "details", "body"),
        incident_type=_args_str(args, "incident_type", "type", "category") or None,
        severity=_args_str(args, "severity") or None,
        title=_args_str(args, "title"),
    )


def _handle_attach_incident_photo(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.ops.incidents import attach_incident_photo

    return attach_incident_photo(
        ctx,
        incident_id=_args_str(args, "incident_id", "id"),
        document_id=_args_str(args, "document_id", "attachment_id"),
        caption=_args_str(args, "caption", "description"),
    )


def _handle_record_invoice(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.ops.invoices import record_invoice

    return record_invoice(
        ctx,
        vendor=_args_str(args, "vendor", "vendor_name"),
        amount=args.get("amount"),
        currency=_args_str(args, "currency") or "",
        invoice_number=_args_str(args, "invoice_number", "number"),
        notes=_args_str(args, "notes", "description", "title", "q"),
        document_id=_args_str(args, "document_id", "attachment_id"),
        photo_url=_args_str(args, "photo_url"),
        category=_args_str(args, "category"),
    )


def _handle_sync_compliance_reminder(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.ops.meetings import sync_compliance_reminder

    return sync_compliance_reminder(
        ctx,
        document_id=_args_str(args, "document_id", "compliance_document_id", "id"),
        q=_args_str(args, "q", "query", "title"),
    )


def _handle_find_staff(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.ops.staff import find_staff

    return find_staff(
        ctx,
        q=_args_str(args, "q", "query", "name"),
        role=_args_str(args, "role"),
        limit=int(args.get("limit") or 10),
    )


def _handle_assign_incident(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.ops.incidents import route_incident

    return route_incident(
        ctx,
        incident_id=_args_str(args, "incident_id", "id"),
        incident_type=_args_str(args, "incident_type", "type"),
    )


def _handle_resolve_incident(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.ops.incidents import resolve_incident

    return resolve_incident(
        ctx,
        incident_id=_args_str(args, "incident_id", "id"),
        q=_args_str(args, "q", "query"),
        resolution_notes=_args_str(args, "resolution_notes", "notes"),
    )


def _handle_assign_category(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.ops.categories import assign_responsibility

    return assign_responsibility(
        ctx,
        category=_args_str(args, "category", "name"),
        owner_name=_args_str(args, "owner_name", "assignee_name", "staff_name"),
        owner_id=_args_str(args, "owner_id", "assignee_id"),
        location_id=_args_str(args, "location_id", "establishment_id"),
    )


def _handle_retrieve_document(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.ops.documents import get_document, show_document

    if args.get("show") or args.get("send"):
        return show_document(
            ctx,
            document_id=_args_str(args, "document_id", "id"),
            q=_args_str(args, "q", "query", "title"),
        )
    return get_document(
        ctx,
        document_id=_args_str(args, "document_id", "id"),
        q=_args_str(args, "q", "query", "title"),
        kind=_args_str(args, "kind"),
    )


def _handle_submit_invoice(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.ops.invoices import request_approval

    return request_approval(
        ctx,
        invoice_id=_args_str(args, "invoice_id", "id"),
        vendor=_args_str(args, "vendor", "q"),
    )


def _handle_approve_invoice(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.ops.invoices import payment_approval_action

    return payment_approval_action(
        ctx,
        action="approve",
        invoice_id=_args_str(args, "invoice_id", "id"),
        vendor=_args_str(args, "vendor"),
        note=_args_str(args, "note"),
    )


def _handle_reject_invoice(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.ops.invoices import payment_approval_action

    return payment_approval_action(
        ctx,
        action="reject",
        invoice_id=_args_str(args, "invoice_id", "id"),
        vendor=_args_str(args, "vendor"),
        note=_args_str(args, "note"),
    )


def _handle_mark_invoice_paid(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.ops.invoices import mark_invoice_paid

    return mark_invoice_paid(
        ctx,
        invoice_id=_args_str(args, "invoice_id", "id"),
        vendor=_args_str(args, "vendor"),
        invoice_number=_args_str(args, "invoice_number"),
        method=_args_str(args, "method"),
        reference=_args_str(args, "reference"),
        amount=args.get("amount"),
        paid_on=args.get("paid_on"),
    )


def _handle_create_reminder(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.ops.meetings import create_personal_reminder

    return create_personal_reminder(
        ctx,
        title=_args_str(args, "title", "text", "q"),
        due_at=_args_str(args, "due_at", "when", "datetime"),
        body=_args_str(args, "body", "description", "notes"),
        recurrence=_args_str(args, "recurrence") or "none",
        reminder_kind=_args_str(args, "reminder_kind", "kind"),
    )


def _handle_create_meeting(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
    from miya.services.ops.meetings import create_calendar_event

    return create_calendar_event(
        ctx,
        title=_args_str(args, "title", "summary"),
        start=_args_str(args, "start", "start_at", "begins_at"),
        end=_args_str(args, "end", "end_at", "ends_at"),
        description=_args_str(args, "description", "body"),
        meeting_kind=_args_str(args, "meeting_kind", "kind"),
    )


def _wave1_handler(tool_name: str):
    def _run(ctx: OpsContext, args: dict[str, Any]) -> OpsResult:
        from miya.services.ops import wave1_mutations as w1

        handler = getattr(w1, tool_name)
        return handler(ctx, **args)

    return _run


_HANDLERS: dict[str, Callable[[OpsContext, dict[str, Any]], OpsResult]] = {
    "get_current_task": _handle_get_current_task,
    "get_current_incident": _handle_get_current_incident,
    "get_current_staff": _handle_get_current_staff,
    "get_current_establishment": _handle_get_current_establishment,
    "get_current_assignment": _handle_get_current_assignment,
    "get_current_document": _handle_get_current_document,
    "get_current_invoice": _handle_get_current_invoice,
    "get_current_reminder": _handle_get_current_reminder,
    "get_current_meeting": _handle_get_current_meeting,
    "recall_operational_memory": _handle_recall_operational_memory,
    "get_event_history": _handle_get_event_history,
    "operational_search": _handle_operational_search,
    "create_task": _handle_create_task,
    "assign_task": _handle_assign_task,
    "update_task_status": _handle_update_task_status,
    "update_task": _handle_update_task,
    "complete_task": _handle_complete_task,
    "create_incident": _handle_create_incident,
    "attach_incident_photo": _handle_attach_incident_photo,
    "record_invoice": _handle_record_invoice,
    "sync_compliance_reminder": _handle_sync_compliance_reminder,
    "find_staff": _handle_find_staff,
    "assign_incident": _handle_assign_incident,
    "resolve_incident": _handle_resolve_incident,
    "assign_category": _handle_assign_category,
    "update_responsibility": _handle_assign_category,
    "retrieve_document": _handle_retrieve_document,
    "submit_invoice": _handle_submit_invoice,
    "approve_invoice": _handle_approve_invoice,
    "reject_invoice": _handle_reject_invoice,
    "mark_invoice_paid": _handle_mark_invoice_paid,
    "create_reminder": _handle_create_reminder,
    "create_meeting": _handle_create_meeting,
    "clock_in": _wave1_handler("staff_clock_in"),
    "clock_out": _wave1_handler("staff_clock_out"),
    "submit_staff_request": _wave1_handler("staff_request"),
    "approve_staff_request": _wave1_handler("approve_staff_request"),
    "reject_staff_request": _wave1_handler("reject_staff_request"),
    "request_time_off": _wave1_handler("request_time_off"),
    "create_shift": _wave1_handler("create_shift"),
    "assign_coverage": _wave1_handler("assign_coverage"),
    "mark_no_show": _wave1_handler("mark_no_show"),
    "assign_invoice": _wave1_handler("assign_invoice"),
    "send_announcement": _wave1_handler("send_announcement"),
    "notify_manager_urgent": _wave1_handler("notify_manager_urgent"),
    "chase_operational_record": _wave1_handler("chase_operational_record"),
    "report_waste": _wave1_handler("report_waste"),
    "update_compliance_document": _wave1_handler("update_compliance_document"),
    "recognize_staff": _wave1_handler("recognize_staff"),
}
