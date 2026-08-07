"""RETRIEVE + REASON — resolve entities from DB / working memory. Never invent IDs."""
from __future__ import annotations

from typing import Any

from miya.services.intelligence.planning.types import (
    ClassifiedIntent,
    Confidence,
    EntityType,
    ExecutionPlan,
    IntentClass,
    PlanAction,
)
from miya.services.ops.context import OpsContext
from miya.services.ops.result import OpsResult


def resolve_plan(
    intent: ClassifiedIntent,
    *,
    ctx: OpsContext,
    session_context: dict[str, Any] | None = None,
) -> ExecutionPlan:
    """Build an execution plan after retrieve/reason. ASK on ambiguity — never guess."""
    workflow = _workflow_for(intent)
    steps = [
        "UNDERSTAND",
        "IDENTIFY",
        "RETRIEVE",
        "REASON",
        "PLAN",
    ]

    if intent.intent == IntentClass.UNKNOWN or not workflow:
        return ExecutionPlan(
            workflow="",
            action=PlanAction.DEFER_TO_AGENT,
            intent=intent,
            steps=steps,
            clarification_message="",
        )

    # Cross-establishment without target
    if "cross_establishment_ambiguity" in intent.reasons:
        locs = ctx.available_locations or []
        if len(locs) > 1 and not intent.slots.get("target_establishment_id"):
            names = ", ".join(r.get("name", "") for r in locs[:8])
            return ExecutionPlan(
                workflow=workflow,
                action=PlanAction.CLARIFY,
                intent=intent,
                steps=steps,
                clarification_message=(
                    f"Which establishment should I use? You have access to: {names}."
                ),
                stage="REASON",
            )

    # Apply explicit establishment hint ("under zamazama") — overrides sticky session default.
    est_hint = str(intent.slots.get("establishment_hint") or "").strip()
    if est_hint:
        _apply_establishment_hint(ctx, est_hint, session_context=session_context)

    # Multi-establishment ops without active context
    if (
        intent.intent in (IntentClass.COMPLETE, IntentClass.ASSIGN, IntentClass.CREATE)
        and not ctx.location_id
        and len(ctx.available_locations or []) > 1
    ):
        names = ", ".join(r.get("name", "") for r in ctx.available_locations[:8])
        _stash_pending_task_mutation(
            intent,
            session_context,
            user=ctx.user,
            restaurant=ctx.restaurant,
        )
        return ExecutionPlan(
            workflow=workflow,
            action=PlanAction.CLARIFY,
            intent=intent,
            steps=steps,
            clarification_message=(
                f"Which establishment is this for? You have access to: {names}."
            ),
            stage="REASON",
        )

    entity_id = ""
    candidates: list[dict[str, Any]] = []

    if intent.entity_type == EntityType.TASK and intent.intent in (
        IntentClass.COMPLETE,
        IntentClass.ASSIGN,
        IntentClass.UPDATE,
        IntentClass.DELETE,
        IntentClass.QUERY,
    ):
        entity_id, candidates, err = _resolve_task(intent, ctx, session_context)
        if err:
            return ExecutionPlan(
                workflow=workflow,
                action=PlanAction.CLARIFY,
                intent=intent,
                steps=steps,
                candidates=candidates,
                clarification_message=err,
                stage="REASON",
            )

    if intent.entity_type == EntityType.INCIDENT and intent.intent in (
        IntentClass.ROUTE,
        IntentClass.QUERY,
        IntentClass.UPDATE,
    ):
        entity_id, candidates, err = _resolve_incident(intent, ctx)
        if err:
            return ExecutionPlan(
                workflow=workflow,
                action=PlanAction.CLARIFY,
                intent=intent,
                steps=steps,
                candidates=candidates,
                clarification_message=err,
                stage="REASON",
            )

    if intent.entity_type == EntityType.DOCUMENT and intent.intent in (
        IntentClass.RETRIEVE,
        IntentClass.QUERY,
        IntentClass.UPDATE,
        IntentClass.REMIND,
        IntentClass.CREATE,
    ):
        entity_id, candidates, err = _resolve_document(intent, ctx, session_context)
        if err:
            return ExecutionPlan(
                workflow=workflow,
                action=PlanAction.CLARIFY,
                intent=intent,
                steps=steps,
                candidates=candidates,
                clarification_message=err,
                stage="REASON",
            )

    if intent.pronoun and not entity_id and intent.entity_type == EntityType.DOCUMENT:
        entity_id, candidates, err = _resolve_document(intent, ctx, session_context, pronoun=True)
        if err:
            return ExecutionPlan(
                workflow=workflow,
                action=PlanAction.CLARIFY,
                intent=intent,
                steps=steps,
                candidates=candidates,
                clarification_message=err,
                stage="REASON",
            )

    if intent.pronoun and not entity_id and intent.entity_type == EntityType.TASK:
        entity_id = _from_working_memory_task(ctx, session_context)
        if not entity_id:
            return ExecutionPlan(
                workflow=workflow,
                action=PlanAction.CLARIFY,
                intent=intent,
                steps=steps,
                clarification_message=(
                    "Which task do you mean? Tell me the title or short ref — "
                    "I won't guess."
                ),
                stage="REASON",
            )

    if intent.pronoun and not entity_id and intent.entity_type == EntityType.INVOICE:
        entity_id = _from_working_set(ctx, "invoices", session_context)
        if not entity_id:
            return ExecutionPlan(
                workflow=workflow,
                action=PlanAction.CLARIFY,
                intent=intent,
                steps=steps,
                clarification_message="Which invoice? Give me the vendor or number.",
                stage="REASON",
            )

    # ASSIGN without assignee
    if intent.intent == IntentClass.ASSIGN and not intent.assignee_hint:
        if re_hr_send(intent.raw_message):
            intent.slots["assign_to_category"] = "HR"
        else:
            return ExecutionPlan(
                workflow=workflow,
                action=PlanAction.CLARIFY,
                intent=intent,
                steps=steps,
                entity_id=entity_id,
                clarification_message="Who should I assign it to?",
                stage="REASON",
            )

    tool_args = _build_tool_args(intent, entity_id)
    action = _decide_action(intent, entity_id, candidates)

    confirm = ""
    if action == PlanAction.CONFIRM:
        label = intent.query or entity_id or "this"
        if intent.intent == IntentClass.COMPLETE:
            confirm = f"Just to confirm — mark *{label}* as completed?"
        elif intent.intent == IntentClass.ASSIGN:
            confirm = f"Assign *{label}* to {intent.assignee_hint}?"
        else:
            confirm = f"Should I proceed with that {intent.intent.value.lower()}?"

    return ExecutionPlan(
        workflow=workflow,
        action=action,
        intent=intent,
        steps=steps + (["EXECUTE", "VERIFY", "RESPOND"] if action == PlanAction.EXECUTE else []),
        entity_id=entity_id,
        entity_ids=[entity_id] if entity_id else [],
        candidates=candidates,
        confirm_message=confirm,
        tool_args=tool_args,
        stage="PLAN",
    )


def re_hr_send(text: str) -> bool:
    import re

    return bool(re.search(r"\bsend\s+(?:this|it|that)\s+to\s+hr\b", text or "", re.I))


def _workflow_for(intent: ClassifiedIntent) -> str:
    # Multimodal same-turn paths (OCR is evidence; workflows execute + verify)
    if intent.slots.get("document_id") or intent.slots.get("multimodal"):
        if intent.intent == IntentClass.CREATE and intent.entity_type == EntityType.INCIDENT:
            return "incident_from_media"
        if intent.intent == IntentClass.CREATE and intent.entity_type == EntityType.INVOICE:
            return "invoice_from_media"
        if intent.intent == IntentClass.REMIND and intent.entity_type in (
            EntityType.REMINDER,
            EntityType.DOCUMENT,
        ):
            return "compliance_reminder_from_media"
        if intent.intent == IntentClass.RETRIEVE and intent.entity_type == EntityType.INCIDENT:
            return "incident_lookup"

    mapping = {
        (IntentClass.COMPLETE, EntityType.TASK): "task_completion",
        (IntentClass.ASSIGN, EntityType.TASK): "task_assignment",
        (IntentClass.ASSIGN, EntityType.CATEGORY): "task_assignment",
        (IntentClass.ROUTE, EntityType.INCIDENT): "incident_routing",
        (IntentClass.CREATE, EntityType.INCIDENT): "incident_routing",  # create+route path
        (IntentClass.CREATE, EntityType.INVOICE): "invoice_from_media",
        (IntentClass.RETRIEVE, EntityType.DOCUMENT): "document_processing",
        (IntentClass.RETRIEVE, EntityType.INCIDENT): "incident_lookup",
        (IntentClass.UPLOAD, EntityType.DOCUMENT): "document_processing",
        (IntentClass.APPROVE, EntityType.INVOICE): "invoice_approval",
        (IntentClass.REJECT, EntityType.INVOICE): "invoice_approval",
        (IntentClass.REMIND, EntityType.REMINDER): "reminder_creation",
        (IntentClass.SCHEDULE, EntityType.MEETING): "meeting_creation",
        (IntentClass.QUERY, EntityType.STAFF): "staff_lookup",
    }
    return mapping.get((intent.intent, intent.entity_type), "")


def _decide_action(
    intent: ClassifiedIntent,
    entity_id: str,
    candidates: list[dict[str, Any]],
) -> PlanAction:
    if candidates and len(candidates) > 1 and not entity_id:
        return PlanAction.CLARIFY
    if intent.confidence == Confidence.LOW and intent.intent not in (
        IntentClass.QUERY,
        IntentClass.RETRIEVE,
        IntentClass.SUMMARIZE,
    ):
        return PlanAction.CLARIFY
    # Consequential mutations at MEDIUM without strong identity → confirm
    consequential = intent.intent in (
        IntentClass.COMPLETE,
        IntentClass.DELETE,
        IntentClass.APPROVE,
        IntentClass.REJECT,
    )
    if consequential and intent.confidence == Confidence.MEDIUM and entity_id:
        return PlanAction.CONFIRM
    if intent.intent in (
        IntentClass.COMPLETE,
        IntentClass.ASSIGN,
        IntentClass.APPROVE,
        IntentClass.REJECT,
        IntentClass.ROUTE,
        IntentClass.REMIND,
        IntentClass.SCHEDULE,
        IntentClass.CREATE,
        IntentClass.RETRIEVE,
        IntentClass.QUERY,
    ):
        if intent.intent == IntentClass.COMPLETE and not entity_id and not intent.query:
            return PlanAction.CLARIFY
        if intent.intent == IntentClass.QUERY and intent.entity_type.value != "staff":
            return PlanAction.DEFER_TO_AGENT
        return PlanAction.EXECUTE
    return PlanAction.DEFER_TO_AGENT


def _resolve_task(
    intent: ClassifiedIntent,
    ctx: OpsContext,
    session_context: dict[str, Any] | None,
) -> tuple[str, list[dict[str, Any]], str]:
    from miya.services.intelligence.entity_resolver import (
        parse_ordinal_reference,
        resolve_entity_reference,
    )

    ordinal = parse_ordinal_reference(intent.raw_message or "")
    if ordinal is not None:
        ref = resolve_entity_reference(
            ctx,
            entity_type="task",
            pronoun_index=ordinal,
            session_context=session_context,
        )
        if ref.needs_clarify:
            err = ref.clarify_message or "Which item do you mean?"
            if "guess" not in err.lower():
                err = f"{err.rstrip('.')} — I won't guess."
            return "", ref.candidates or [], err
        if ref.entity_id:
            return ref.entity_id, [], ""

    ref = resolve_entity_reference(
        ctx,
        entity_type="task",
        query=intent.query or "",
        pronoun=bool(intent.pronoun),
        session_context=session_context,
    )
    if ref.needs_clarify:
        err = ref.clarify_message or "Several tasks match — which one? Reply with the title or #ref. I won't guess."
        if "guess" not in err.lower():
            err = f"{err.rstrip('.')} — I won't guess."
        return "", ref.candidates or [], err
    if ref.entity_id:
        return ref.entity_id, [], ""
    err = ref.clarify_message or f"I couldn't find a task matching '{intent.query or ''}'."
    return "", [], err


def _apply_establishment_hint(
    ctx: OpsContext,
    hint: str,
    *,
    session_context: dict[str, Any] | None,
) -> None:
    from miya.services.ops.establishments import set_establishment_context

    result = set_establishment_context(ctx, q=hint)
    if result.success:
        patch = (result.data or {}).get("session_patch") or {}
        if patch.get("location_id"):
            ctx.location_id = str(patch["location_id"])
            ctx.location_name = patch.get("location_name") or ctx.location_name
            if session_context is not None:
                session_context["location_id"] = ctx.location_id
                session_context["location_name"] = ctx.location_name


def _stash_pending_task_mutation(
    intent: ClassifiedIntent,
    session_context: dict[str, Any] | None,
    *,
    user=None,
    restaurant=None,
) -> None:
    if not session_context:
        return
    if intent.intent not in (IntentClass.COMPLETE, IntentClass.ASSIGN, IntentClass.UPDATE):
        return
    if intent.entity_type != EntityType.TASK:
        return
    payload = {
        "raw_message": intent.raw_message,
        "query": intent.query or "",
        "intent": intent.intent.value,
        "status_hint": intent.status_hint or "",
    }
    session_context["_pending_task_mutation"] = payload
    if user is not None and restaurant is not None:
        from miya.services.intelligence.pending_mutation import persist_pending_task_mutation

        persist_pending_task_mutation(user=user, restaurant=restaurant, payload=payload)


def _resolve_document(
    intent: ClassifiedIntent,
    ctx: OpsContext,
    session_context: dict[str, Any] | None,
    *,
    pronoun: bool = False,
) -> tuple[str, list[dict[str, Any]], str]:
    from miya.services.intelligence.document_entity_linking import (
        DocumentResolutionState,
        resolve_document_reference,
    )
    from miya.services.intelligence.entity_resolver import _mutation_sensitive_query

    mutation_sensitive = intent.intent in (
        IntentClass.UPDATE,
        IntentClass.CREATE,
        IntentClass.REMIND,
    ) or _mutation_sensitive_query(intent.raw_message or intent.query or "")

    ref = resolve_document_reference(
        ctx,
        document_id=str(intent.slots.get("document_id") or ""),
        document_family_id=str(intent.slots.get("document_family_id") or ""),
        query=intent.query or "",
        raw_message=intent.raw_message or "",
        session_context=session_context,
        category=str(intent.slots.get("category") or ""),
        vendor=str(intent.slots.get("vendor") or ""),
        mutation_sensitive=mutation_sensitive,
        pronoun=pronoun or bool(intent.pronoun),
    )
    if ref.state == DocumentResolutionState.RESOLVED:
        intent.slots.setdefault("document_id", ref.document_id)
        if ref.document_family_id:
            intent.slots.setdefault("document_family_id", ref.document_family_id)
        intent.slots.setdefault("document_resolution", ref.to_dict())
        return ref.document_id, ref.candidates or [], ""
    if ref.state == DocumentResolutionState.AMBIGUOUS:
        msg = ref.clarify_message or "Which document do you mean?"
        if "won't guess" not in msg.lower():
            msg = f"{msg.rstrip('.')} — I won't guess."
        return "", ref.candidates or [], msg
    if ref.state == DocumentResolutionState.NOT_FOUND:
        if intent.intent in (IntentClass.RETRIEVE, IntentClass.QUERY):
            return "", [], ""
    return "", [], ref.clarify_message or "I couldn't find that document."


def _resolve_incident(
    intent: ClassifiedIntent,
    ctx: OpsContext,
) -> tuple[str, list[dict[str, Any]], str]:
    from miya.services.ops.incidents import get_incident

    result = get_incident(ctx, q=intent.query or intent.raw_message)
    if result.success:
        inc = (result.data or {}).get("incident") or {}
        return str(inc.get("id") or ""), [], ""
    if result.needs_clarification:
        return "", (result.data or {}).get("incidents") or [], result.message_for_user
    return "", [], result.message_for_user or "Which incident?"


def _from_working_memory_task(ctx: OpsContext, session_context: dict[str, Any] | None) -> str:
    try:
        from miya.services.intelligence.working_memory import get_working_memory

        wm = get_working_memory(user=ctx.user, restaurant=ctx.restaurant)
        tid = str(wm.get("current_task_id") or "")
        if tid:
            return tid
    except Exception:
        pass
    return _from_working_set(ctx, "tasks", session_context)


def _from_working_set(
    ctx: OpsContext,
    kind: str,
    session_context: dict[str, Any] | None,
) -> str:
    try:
        from miya.services.working_set import resolve_ids

        ids = resolve_ids(
            restaurant_id=ctx.restaurant_id,
            user_id=ctx.user_id,
            kind=kind,
            pronoun_hint="it",
        )
        if len(ids) == 1:
            return ids[0]
        # Prefer first only when exactly one — never guess among many
    except Exception:
        pass
    return ""


def _build_tool_args(intent: ClassifiedIntent, entity_id: str) -> dict[str, Any]:
    args: dict[str, Any] = {}
    if entity_id:
        if intent.entity_type == EntityType.TASK:
            args["task_id"] = entity_id
        elif intent.entity_type == EntityType.INCIDENT:
            args["incident_id"] = entity_id
        elif intent.entity_type == EntityType.INVOICE:
            args["invoice_id"] = entity_id
        elif intent.entity_type == EntityType.DOCUMENT:
            args["document_id"] = entity_id
    if intent.query:
        args.setdefault("q", intent.query)
        args.setdefault("title", intent.query)
    if intent.assignee_hint:
        args["assignee_name"] = intent.assignee_hint
    if intent.status_hint:
        args["status"] = intent.status_hint
    if intent.slots.get("assign_to_category"):
        args["assign_to_category"] = intent.slots["assign_to_category"]
    if intent.intent == IntentClass.CREATE and intent.entity_type == EntityType.INCIDENT:
        args["description"] = intent.raw_message or intent.query or "Incident from attachment"
    if intent.intent == IntentClass.REMIND:
        args["title"] = intent.query or intent.slots.get("attachment_title") or "Reminder"
    if intent.intent == IntentClass.APPROVE:
        args["action"] = "approve"
    if intent.intent == IntentClass.REJECT:
        args["action"] = "reject"
    # Multimodal evidence slots (OCR is input to reason, not authority)
    for key in (
        "document_id",
        "structured",
        "summary",
        "vendor",
        "amount",
        "currency",
        "invoice_number",
        "expiry_date",
        "invoice_id",
        "compliance_document_id",
        "media_kind",
        "attachment_title",
    ):
        if intent.slots.get(key) not in (None, ""):
            args[key] = intent.slots[key]
    if intent.intent == IntentClass.QUERY and intent.entity_type == EntityType.STAFF:
        args["q"] = intent.query or intent.raw_message
    return args
