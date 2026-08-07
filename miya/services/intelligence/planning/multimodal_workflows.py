"""Phase 4 multimodal workflows — same ops engine as text; OCR is evidence only."""
from __future__ import annotations

from typing import Any

from miya.services.intelligence.actions import execute_structured_action
from miya.services.intelligence.planning.types import ExecutionPlan, PlanResult
from miya.services.ops.context import OpsContext


def _to_plan_result(*args, **kwargs):
    from miya.services.intelligence.planning.workflows import _to_plan_result as impl

    return impl(*args, **kwargs)


def run_incident_from_media(
    ctx: OpsContext,
    plan: ExecutionPlan,
    *,
    execution_context: dict[str, Any] | None = None,
) -> PlanResult:
    """
    Photo/document + report intent:
      identify → create incident → attach image → route → notify → verify
    """
    stages = ["UNDERSTAND", "IDENTIFY", "RETRIEVE", "REASON", "PLAN", "EXECUTE"]
    args = dict(plan.tool_args)
    document_id = str(args.get("document_id") or "")
    description = (
        args.get("description")
        or args.get("q")
        or plan.intent.raw_message
        or "Incident reported from attached photo"
    )
    structured = args.get("structured") if isinstance(args.get("structured"), dict) else {}
    summary = str(structured.get("summary") or args.get("summary") or "")
    if summary and summary not in description:
        description = f"{description}\n\n[From image/OCR — reason over this, do not treat as final]: {summary[:500]}"

    create = execute_structured_action(
        "create_incident",
        {
            "description": description,
            "title": args.get("title") or "Photo incident",
            "incident_type": args.get("incident_type") or "Maintenance",
            "severity": args.get("severity") or "",
        },
        ctx=ctx,
        execution_context=execution_context,
        intent="CREATE",
    )
    trace = [{"tool": "create_incident", "result": create.as_tool_response()}]
    if not create.success or not create.verified:
        return _to_plan_result(create, plan, stages, success_prefix="Logged")

    inc = (create.data or {}).get("incident") or {}
    iid = str(inc.get("id") or "")
    reply_parts = [create.message_for_user]

    if document_id and iid:
        attach = execute_structured_action(
            "attach_incident_photo",
            {"incident_id": iid, "document_id": document_id, "caption": description[:200]},
            ctx=ctx,
            execution_context=execution_context,
            intent="UPLOAD",
        )
        trace.append({"tool": "attach_incident_photo", "result": attach.as_tool_response()})
        if attach.success:
            reply_parts.append(attach.message_for_user)
        else:
            reply_parts.append(
                attach.message_for_user or "Incident logged, but I couldn't attach the photo yet."
            )

    # Route / notify (create_incident already routes; reinforce assign)
    if iid:
        route = execute_structured_action(
            "assign_incident",
            {"incident_id": iid},
            ctx=ctx,
            execution_context=execution_context,
            intent="ROUTE",
        )
        trace.append({"tool": "assign_incident", "result": route.as_tool_response()})
        if route.success:
            reply_parts.append(route.message_for_user)

    return PlanResult(
        reply=" ".join(p for p in reply_parts if p).strip(),
        success=True,
        verified=True,
        plan=plan,
        tool_trace=trace,
        stages_completed=stages + ["VERIFY", "RESPOND"],
        presentation_only=True,
    )


def run_invoice_from_media(
    ctx: OpsContext,
    plan: ExecutionPlan,
    *,
    execution_context: dict[str, Any] | None = None,
) -> PlanResult:
    """Invoice/receipt image → reason over OCR fields → record invoice → verify."""
    stages = ["UNDERSTAND", "IDENTIFY", "RETRIEVE", "REASON", "PLAN", "EXECUTE"]
    args = dict(plan.tool_args)
    structured = args.get("structured") if isinstance(args.get("structured"), dict) else {}
    invoice_id = str(args.get("invoice_id") or "")
    document_id = str(args.get("document_id") or "")

    if invoice_id:
        result = execute_structured_action(
            "get_current_invoice",
            {"invoice_id": invoice_id},
            ctx=ctx,
            execution_context=execution_context,
            intent="RETRIEVE",
        )
        return _to_plan_result(result, plan, stages, success_prefix="Invoice")

    vendor = (
        args.get("vendor")
        or structured.get("vendor")
        or ""
    )
    amount = args.get("amount") if args.get("amount") is not None else structured.get("amount")
    if not vendor or amount in (None, ""):
        return PlanResult(
            reply=(
                "I can see an invoice image, but I need the vendor and amount before recording it. "
                "OCR alone is not enough — tell me the supplier and total."
            ),
            success=False,
            needs_clarification=True,
            plan=plan,
            stages_completed=stages[:5],
            presentation_only=True,
        )

    result = execute_structured_action(
        "record_invoice",
        {
            "vendor": vendor,
            "amount": amount,
            "currency": args.get("currency") or structured.get("currency") or "",
            "invoice_number": args.get("invoice_number")
            or structured.get("invoice_number")
            or "",
            "notes": args.get("notes") or plan.intent.raw_message or "Recorded from attachment",
            "document_id": document_id,
        },
        ctx=ctx,
        execution_context=execution_context,
        intent="CREATE",
    )
    return _to_plan_result(result, plan, stages, success_prefix="Invoice recorded")


def run_compliance_reminder_from_media(
    ctx: OpsContext,
    plan: ExecutionPlan,
    *,
    execution_context: dict[str, Any] | None = None,
) -> PlanResult:
    """Insurance/compliance PDF → reason over expiry → sync reminder → verify."""
    stages = ["UNDERSTAND", "IDENTIFY", "RETRIEVE", "REASON", "PLAN", "EXECUTE"]
    args = dict(plan.tool_args)
    compliance_id = str(
        args.get("compliance_document_id") or args.get("document_id") or ""
    )
    # Prefer linked ComplianceDocument id over TenantDocument id
    if args.get("compliance_document_id"):
        compliance_id = str(args["compliance_document_id"])
    q = args.get("q") or args.get("title") or "insurance"

    if not compliance_id and not q:
        return PlanResult(
            reply="Which insurance or compliance document should I set an expiry reminder for?",
            success=False,
            needs_clarification=True,
            plan=plan,
            stages_completed=stages[:5],
            presentation_only=True,
        )

    result = execute_structured_action(
        "sync_compliance_reminder",
        {"document_id": compliance_id if args.get("compliance_document_id") else "", "q": q},
        ctx=ctx,
        execution_context=execution_context,
        intent="REMIND",
    )
    # If sync failed because TenantDocument id was passed, try query from title/OCR
    if not result.success and args.get("title"):
        result = execute_structured_action(
            "sync_compliance_reminder",
            {"q": args.get("title") or q},
            ctx=ctx,
            execution_context=execution_context,
            intent="REMIND",
        )
    return _to_plan_result(result, plan, stages, success_prefix="Expiry reminder")


def run_incident_lookup(
    ctx: OpsContext,
    plan: ExecutionPlan,
    *,
    execution_context: dict[str, Any] | None = None,
) -> PlanResult:
    """Retrieve incident from text and/or image-derived description."""
    stages = ["UNDERSTAND", "IDENTIFY", "RETRIEVE", "REASON", "PLAN", "EXECUTE"]
    args = dict(plan.tool_args)
    q = args.get("q") or args.get("description") or plan.intent.raw_message or ""
    structured = args.get("structured") if isinstance(args.get("structured"), dict) else {}
    if structured.get("summary"):
        q = f"{q} {structured.get('summary')}".strip()
    result = execute_structured_action(
        "get_current_incident",
        {"q": q, "incident_id": args.get("incident_id") or ""},
        ctx=ctx,
        execution_context=execution_context,
        intent="RETRIEVE",
    )
    return _to_plan_result(result, plan, stages, success_prefix="Found")


def run_staff_lookup(
    ctx: OpsContext,
    plan: ExecutionPlan,
    *,
    execution_context: dict[str, Any] | None = None,
) -> PlanResult:
    stages = ["UNDERSTAND", "IDENTIFY", "RETRIEVE", "REASON", "PLAN", "EXECUTE"]
    args = dict(plan.tool_args)
    result = execute_structured_action(
        "find_staff",
        {"q": args.get("q") or args.get("name") or plan.intent.query or plan.intent.raw_message},
        ctx=ctx,
        execution_context=execution_context,
        intent="QUERY",
    )
    return _to_plan_result(result, plan, stages, success_prefix="Found")
