"""Gateway: map Miya tool names → canonical ops services."""
from __future__ import annotations

from typing import Any

from miya.services.ops.context import OpsContext
from miya.services.ops.result import OpsResult, fail


def _normalize_task_lookup_args(
    task_id: str,
    q: str,
    *,
    title: str = "",
) -> tuple[str, str]:
    """Move title-like task_id values into q — agents often put titles in task_id."""
    from core.canonical.tasks import is_record_id

    task_id = (task_id or "").strip()
    q = (q or title or "").strip()
    if task_id and not is_record_id(task_id):
        q = q or task_id
        task_id = ""
    return task_id, q


# Tools handled entirely by the canonical ops layer (no HTTP self-call).
CANONICAL_TOOL_NAMES = frozenset(
    {
        "find_staff",
        "staff_lookup",
        "find_tasks",
        "get_dashboard_task",
        "find_incidents",
        "list_incidents",
        "find_category",
        "find_category_owners",
        "find_responsible_people",
        "assign_responsibility",
        "create_responsibility_category",
        "create_category",
        "route_responsibility_event",
        "find_establishment",
        "find_establishments",
        "set_establishment_context",
        "switch_establishment",
        "find_documents",
        "list_tenant_documents",
        "get_tenant_document",
        "get_document",
        "show_document",
        "query_document_intelligence",
        "find_invoices",
        "get_invoice",
        "get_invoice_timeline",
        "record_invoice",
        "payment_approval",
        "request_invoice_approval",
        "check_invoice_approval",
        "mark_invoice_paid",
        "attach_invoice_proof",
        "return_invoice",
        "retrieve_operational_history",
        "recall_operational_memory",
        "get_event_history",
        "get_entity_history",
        "get_current_entity_state",
        "create_ops_task",
        "assign_ops_task",
        "update_ops_task_status",
        # Prefer canonical path for these write tools too:
        "create_dashboard_task",
        "reassign_dashboard_task",
        "update_dashboard_task_status",
        "update_dashboard_task",
        "report_incident",
        "create_incident",
        "route_incident",
        "get_incident",
        "get_incident_photo",
        "attach_incident_photo",
        "close_incident",
        "resolve_incident",
        "confirm_meeting",
        "list_meetings",
        "list_calendar_events",
        "create_calendar_event",
        "update_calendar_event",
        "delete_calendar_event",
        "create_personal_reminder",
        "list_reminders",
        "cancel_reminder",
        "sync_compliance_reminder",
        "category_routing",
        # Phase 11 Wave 1 — legacy mutation migrations
        "staff_clock_in",
        "staff_clock_out",
        "staff_request",
        "approve_staff_request",
        "reject_staff_request",
        "request_time_off",
        "create_shift",
        "assign_coverage",
        "mark_no_show",
        "assign_invoice",
        "send_announcement",
        "notify_manager_urgent",
        "chase_operational_record",
        "report_waste",
        "update_compliance_document",
        "recognize_staff",
    }
)


def build_ops_context(*, user, restaurant, session_context: dict[str, Any] | None) -> OpsContext | None:
    if user is None:
        return None
    if restaurant is None:
        restaurant = getattr(user, "restaurant", None)
    if restaurant is None:
        return None
    return OpsContext.from_session(user=user, restaurant=restaurant, session_context=session_context)


def dispatch_canonical_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    ctx: OpsContext,
) -> OpsResult | None:
    """
    Return OpsResult if this tool is handled by the ops layer.
    Return None to fall through to legacy HTTP dispatch.
    """
    args = dict(arguments or {})

    if name in ("find_staff", "staff_lookup"):
        from miya.services.ops.staff import find_staff

        return find_staff(
            ctx,
            name=str(args.get("name") or ""),
            role=str(args.get("role") or ""),
            tag=str(args.get("tag") or args.get("department") or ""),
            q=str(args.get("q") or args.get("query") or ""),
            limit=int(args.get("limit") or 20),
        )

    if name in ("find_tasks", "get_dashboard_task"):
        from miya.services.ops.tasks import find_tasks, get_task_state

        if name == "get_dashboard_task" or args.get("task_id") or args.get("task_ref"):
            task_id = str(args.get("task_id") or args.get("task_ref") or args.get("id") or "")
            q = str(args.get("q") or args.get("title") or args.get("query") or "")
            task_id, q = _normalize_task_lookup_args(task_id, q, title=str(args.get("title") or ""))
            return get_task_state(
                ctx,
                task_id=task_id,
                q=q,
                title=str(args.get("title") or ""),
                assignee_name=str(args.get("assignee_name") or args.get("staff_name") or ""),
            )
        return find_tasks(
            ctx,
            q=str(args.get("q") or args.get("query") or args.get("title") or ""),
            status=str(args.get("status") or ""),
            assignee_name=str(args.get("assignee_name") or args.get("staff_name") or ""),
            task_id=str(args.get("task_id") or ""),
            include_custom_widgets=bool(args.get("include_custom_widgets", True)),
            limit=int(args.get("limit") or 20),
        )

    if name in ("find_incidents", "list_incidents"):
        from miya.services.ops.incidents import find_incidents

        days = args.get("days")
        try:
            days_i = int(days) if days is not None else None
        except (TypeError, ValueError):
            days_i = None
        return find_incidents(
            ctx,
            q=str(args.get("q") or args.get("query") or ""),
            status=str(args.get("status") or ""),
            since=str(args.get("since") or ""),
            days=days_i,
            limit=int(args.get("limit") or 20),
        )

    if name in ("report_incident", "create_incident"):
        from miya.services.ops.incidents import create_incident

        return create_incident(
            ctx,
            description=str(args.get("description") or args.get("message") or args.get("q") or ""),
            incident_type=str(args.get("incident_type") or args.get("category") or args.get("type") or "") or None,
            severity=str(args.get("severity") or "") or None,
            title=str(args.get("title") or ""),
        )

    if name == "route_incident":
        from miya.services.ops.incidents import route_incident

        return route_incident(
            ctx,
            incident_id=str(args.get("incident_id") or args.get("id") or ""),
            incident_type=str(args.get("incident_type") or args.get("category") or ""),
        )

    if name == "get_incident":
        from miya.services.ops.incidents import get_incident

        return get_incident(
            ctx,
            incident_id=str(args.get("incident_id") or args.get("id") or ""),
            q=str(args.get("q") or args.get("query") or args.get("title") or ""),
        )

    if name == "get_incident_photo":
        from miya.services.ops.incidents import get_incident_photo

        return get_incident_photo(
            ctx,
            incident_id=str(args.get("incident_id") or args.get("id") or ""),
            q=str(args.get("q") or args.get("query") or args.get("title") or ""),
            index=int(args.get("index") or args.get("photo_index") or 0),
            phone=str(args.get("phone") or ""),
        )

    if name == "attach_incident_photo":
        from miya.services.ops.incidents import attach_incident_photo

        return attach_incident_photo(
            ctx,
            incident_id=str(args.get("incident_id") or args.get("id") or ""),
            document_id=str(args.get("document_id") or args.get("attachment_id") or ""),
            caption=str(args.get("caption") or args.get("description") or ""),
        )

    if name in ("close_incident", "resolve_incident"):
        from miya.services.ops.incidents import resolve_incident

        return resolve_incident(
            ctx,
            incident_id=str(args.get("incident_id") or args.get("id") or ""),
            q=str(args.get("q") or args.get("query") or args.get("title") or ""),
            resolution_notes=str(args.get("resolution_notes") or args.get("notes") or ""),
        )

    if name == "confirm_meeting":
        from miya.services.ops.meetings import confirm_meeting

        return confirm_meeting(
            ctx,
            q=str(args.get("q") or args.get("title") or args.get("query") or ""),
            event_id=str(args.get("event_id") or args.get("id") or ""),
        )

    if name in ("list_meetings", "list_calendar_events"):
        from miya.services.ops.meetings import list_calendar_events, list_meetings

        days = args.get("days")
        try:
            days_i = int(days) if days is not None else 14
        except (TypeError, ValueError):
            days_i = 14
        if name == "list_calendar_events":
            return list_calendar_events(
                ctx,
                q=str(args.get("q") or args.get("query") or args.get("title") or ""),
                days=days_i,
                limit=int(args.get("limit") or 20),
            )
        return list_meetings(
            ctx,
            q=str(args.get("q") or args.get("query") or args.get("title") or ""),
            meeting_kind=str(args.get("meeting_kind") or args.get("department") or args.get("kind") or ""),
            days=days_i,
            limit=int(args.get("limit") or 20),
        )

    if name == "create_calendar_event":
        from miya.services.ops.meetings import create_calendar_event

        batch = args.get("events") or args.get("meetings") or args.get("appointments")
        attendees = args.get("attendees") or []
        if not isinstance(attendees, list):
            attendees = [attendees] if attendees else []
        return create_calendar_event(
            ctx,
            title=str(args.get("title") or args.get("summary") or ""),
            start=str(args.get("start") or args.get("start_at") or args.get("startTime") or ""),
            end=str(args.get("end") or args.get("end_at") or args.get("endTime") or ""),
            description=str(args.get("description") or args.get("notes") or ""),
            location=str(args.get("location") or ""),
            meeting_kind=str(args.get("meeting_kind") or args.get("department") or args.get("kind") or ""),
            is_reminder=args.get("is_reminder", False) in (True, "true", "1", 1, "yes"),
            events=batch if isinstance(batch, list) else None,
            attendees=attendees,
        )

    if name == "update_calendar_event":
        from miya.services.ops.meetings import update_calendar_event

        return update_calendar_event(
            ctx,
            event_id=str(args.get("event_id") or args.get("id") or ""),
            q=str(args.get("q") or args.get("query") or args.get("title") or ""),
            title=str(args.get("title") or args.get("summary") or ""),
            start=str(args.get("start") or args.get("start_at") or ""),
            end=str(args.get("end") or args.get("end_at") or ""),
            location=str(args.get("location") or ""),
            description=str(args.get("description") or args.get("notes") or ""),
        )

    if name == "delete_calendar_event":
        from miya.services.ops.meetings import delete_calendar_event

        return delete_calendar_event(
            ctx,
            event_id=str(args.get("event_id") or args.get("id") or ""),
            q=str(args.get("q") or args.get("query") or args.get("title") or ""),
        )

    if name == "create_personal_reminder":
        from miya.services.ops.meetings import create_personal_reminder

        return create_personal_reminder(
            ctx,
            title=str(args.get("title") or args.get("text") or ""),
            due_at=str(args.get("due_at") or args.get("when") or args.get("remind_at") or ""),
            body=str(args.get("body") or args.get("description") or ""),
            recurrence=str(args.get("recurrence") or "none"),
            reminder_kind=str(args.get("reminder_kind") or args.get("kind") or ""),
        )

    if name == "list_reminders":
        from miya.services.ops.meetings import list_reminders

        return list_reminders(
            ctx,
            q=str(args.get("q") or args.get("query") or ""),
            status=str(args.get("status") or "pending"),
            limit=int(args.get("limit") or 20),
        )

    if name == "cancel_reminder":
        from miya.services.ops.meetings import cancel_reminder

        return cancel_reminder(
            ctx,
            reminder_id=str(args.get("reminder_id") or args.get("id") or ""),
            q=str(args.get("q") or args.get("query") or args.get("title") or ""),
        )

    if name == "sync_compliance_reminder":
        from miya.services.ops.meetings import sync_compliance_reminder

        return sync_compliance_reminder(
            ctx,
            document_id=str(args.get("document_id") or args.get("id") or ""),
            q=str(args.get("q") or args.get("query") or args.get("title") or ""),
        )

    if name in ("find_category", "find_category_owners", "find_responsible_people", "category_owners"):
        from miya.services.ops.categories import find_category_owners
        from miya.services.ops.incidents import find_incident_responsible

        cat = str(args.get("category") or args.get("q") or args.get("name") or "")
        if str(args.get("kind") or "").lower() == "incident" or "incident" in cat.lower():
            return find_incident_responsible(ctx, incident_type=cat, q=cat)
        return find_category_owners(
            ctx,
            category=cat,
            q=cat,
            location_id=str(args.get("location_id") or ctx.location_id or ""),
        )

    if name == "assign_responsibility":
        from miya.services.ops.categories import assign_responsibility

        owner_ids = args.get("owner_ids") or args.get("assignee_ids") or []
        if not isinstance(owner_ids, list):
            owner_ids = [owner_ids] if owner_ids else []
        owner_names = args.get("owner_names") or args.get("staff_names") or []
        if not isinstance(owner_names, list):
            owner_names = [owner_names] if owner_names else []
        return assign_responsibility(
            ctx,
            category=str(args.get("category") or args.get("q") or ""),
            owner_name=str(args.get("owner_name") or args.get("staff_name") or args.get("name") or ""),
            owner_id=str(args.get("owner_id") or args.get("assignee_id") or ""),
            owner_ids=owner_ids,
            owner_names=owner_names,
            location_id=str(args.get("location_id") or ctx.location_id or ""),
            strategy=str(args.get("strategy") or ""),
            replace=args.get("replace", True) not in (False, "false", "0", 0),
        )

    if name in ("create_responsibility_category", "create_category"):
        from miya.services.ops.categories import create_category

        slugs = args.get("slugs") or []
        if not isinstance(slugs, list):
            slugs = []
        return create_category(
            ctx,
            code=str(args.get("code") or args.get("category") or args.get("name") or ""),
            label=str(args.get("label") or args.get("title") or ""),
            kind=str(args.get("kind") or "request"),
            slugs=slugs,
        )

    if name == "route_responsibility_event":
        from miya.services.ops.categories import route_responsibility_event

        return route_responsibility_event(
            ctx,
            category=str(args.get("category") or args.get("q") or ""),
            kind=str(args.get("kind") or "task"),
            title=str(args.get("title") or ""),
            create_task=bool(args.get("create_task")),
            task_description=str(args.get("description") or ""),
            entity_id=str(args.get("entity_id") or args.get("id") or ""),
            location_id=str(args.get("location_id") or ctx.location_id or ""),
            notify=args.get("notify", True) not in (False, "false", "0", 0),
        )

    if name == "category_routing":
        # GET → find owners; SET → assign_responsibility / set_responsible_people
        act = str(args.get("action") or "get").lower()
        if act == "get":
            from miya.services.ops.categories import find_category_owners

            cat = str(args.get("category") or args.get("q") or "")
            if cat:
                return find_category_owners(ctx, category=cat, location_id=str(args.get("location_id") or ""))
            # Fall through to HTTP for full map
            return None
        if act == "set":
            from miya.services.ops.categories import assign_responsibility

            owners_obj = args.get("category_owners") or {}
            if isinstance(owners_obj, dict) and owners_obj:
                # Apply first key (tool often sets one category); multi-key via agent_department_owners
                cat, val = next(iter(owners_obj.items()))
                ids = val if isinstance(val, list) else [val]
                return assign_responsibility(
                    ctx,
                    category=str(cat),
                    owner_ids=[str(x) for x in ids if x],
                    location_id=str(args.get("location_id") or ""),
                    strategy=str(args.get("strategy") or ""),
                )
            return assign_responsibility(
                ctx,
                category=str(args.get("category") or ""),
                owner_id=str(args.get("owner_id") or args.get("assignee_id") or ""),
                owner_name=str(args.get("owner_name") or args.get("staff_name") or ""),
                location_id=str(args.get("location_id") or ""),
            )
        return None

    if name in ("find_establishment", "find_establishments"):
        from miya.services.ops.establishments import find_establishments

        return find_establishments(ctx, q=str(args.get("q") or ""), name=str(args.get("name") or ""))

    if name in ("set_establishment_context", "switch_establishment"):
        from miya.services.ops.establishments import set_establishment_context

        return set_establishment_context(
            ctx,
            location_id=str(args.get("location_id") or args.get("id") or ""),
            q=str(args.get("q") or args.get("query") or ""),
            name=str(args.get("name") or args.get("location_name") or ""),
        )

    if name in ("find_documents", "list_tenant_documents"):
        from miya.services.ops.documents import find_documents

        days = args.get("days")
        try:
            days_i = int(days) if days is not None else None
        except (TypeError, ValueError):
            days_i = None
        kind = str(args.get("kind") or "")
        if name == "list_tenant_documents" and not kind:
            kind = "tenant"
        return find_documents(
            ctx,
            q=str(args.get("q") or args.get("query") or ""),
            kind=kind,
            category=str(args.get("category") or ""),
            since=str(args.get("since") or ""),
            days=days_i,
            limit=int(args.get("limit") or 20),
        )

    if name in ("get_document", "get_tenant_document"):
        from miya.services.ops.documents import get_document

        return get_document(
            ctx,
            document_id=str(args.get("document_id") or args.get("id") or ""),
            q=str(args.get("q") or args.get("query") or args.get("title") or ""),
            kind=str(args.get("kind") or ("tenant" if name == "get_tenant_document" else "")),
        )

    if name == "show_document":
        from miya.services.ops.documents import show_document

        return show_document(
            ctx,
            document_id=str(args.get("document_id") or args.get("id") or ""),
            q=str(args.get("q") or args.get("query") or args.get("title") or ""),
            phone=str(args.get("phone") or ""),
        )

    if name == "query_document_intelligence":
        from miya.services.ops.documents import query_document_intelligence

        days = args.get("days")
        try:
            days_i = int(days) if days is not None else None
        except (TypeError, ValueError):
            days_i = None
        return query_document_intelligence(
            ctx,
            q=str(args.get("q") or args.get("query") or ""),
            question=str(args.get("question") or args.get("user_message") or ""),
            document_id=str(args.get("document_id") or args.get("id") or ""),
            since=str(args.get("since") or ""),
            days=days_i,
        )

    if name == "find_invoices":
        from miya.services.ops.invoices import find_invoices

        days = args.get("days")
        try:
            days_i = int(days) if days is not None else None
        except (TypeError, ValueError):
            days_i = None
        return find_invoices(
            ctx,
            q=str(args.get("q") or args.get("query") or ""),
            vendor=str(args.get("vendor") or args.get("vendor_name") or args.get("supplier") or ""),
            status=str(args.get("status") or ""),
            since=str(args.get("since") or ""),
            days=days_i,
            limit=int(args.get("limit") or 20),
        )

    if name == "get_invoice":
        from miya.services.ops.invoices import get_invoice

        return get_invoice(
            ctx,
            invoice_id=str(args.get("invoice_id") or args.get("id") or ""),
            vendor=str(args.get("vendor") or args.get("vendor_name") or args.get("supplier") or ""),
            invoice_number=str(args.get("invoice_number") or args.get("number") or ""),
            q=str(args.get("q") or args.get("query") or ""),
        )

    if name == "get_invoice_timeline":
        from miya.services.ops.invoices import get_invoice_timeline

        return get_invoice_timeline(
            ctx,
            invoice_id=str(args.get("invoice_id") or args.get("id") or ""),
            vendor=str(args.get("vendor") or args.get("vendor_name") or args.get("supplier") or ""),
            invoice_number=str(args.get("invoice_number") or args.get("number") or ""),
            q=str(args.get("q") or args.get("query") or ""),
        )

    if name == "record_invoice":
        from miya.services.ops.invoices import record_invoice

        return record_invoice(
            ctx,
            vendor=str(args.get("vendor") or args.get("vendor_name") or args.get("supplier") or ""),
            amount=args.get("amount") or args.get("total"),
            due_date=args.get("due_date") or args.get("dueDate"),
            currency=str(args.get("currency") or ""),
            invoice_number=str(args.get("invoice_number") or args.get("number") or ""),
            issue_date=args.get("issue_date") or args.get("issueDate"),
            notes=str(args.get("notes") or args.get("description") or ""),
            location_id=str(args.get("location_id") or args.get("location") or ""),
            location_name=str(args.get("location_name") or ""),
            photo_url=str(args.get("photo_url") or args.get("photoUrl") or ""),
            category=str(args.get("category") or ""),
            start_approval=args.get("start_approval", True) not in (False, "false", "0", 0),
            document_id=str(args.get("document_id") or args.get("attachment_id") or ""),
        )

    if name in ("payment_approval", "request_invoice_approval"):
        from miya.services.ops.invoices import payment_approval_action, request_approval

        if name == "request_invoice_approval":
            return request_approval(
                ctx,
                invoice_id=str(args.get("invoice_id") or args.get("id") or ""),
                vendor=str(args.get("vendor") or args.get("vendor_name") or ""),
            )
        return payment_approval_action(
            ctx,
            action=str(args.get("action") or "list"),
            invoice_id=str(args.get("invoice_id") or args.get("id") or ""),
            vendor=str(args.get("vendor") or args.get("vendor_name") or args.get("supplier") or ""),
            note=str(args.get("note") or args.get("notes") or args.get("reason") or ""),
        )

    if name == "check_invoice_approval":
        from miya.services.ops.invoices import check_amount_and_tier

        return check_amount_and_tier(
            ctx,
            invoice_id=str(args.get("invoice_id") or args.get("id") or ""),
            vendor=str(args.get("vendor") or args.get("vendor_name") or ""),
        )

    if name == "mark_invoice_paid":
        from miya.services.ops.invoices import mark_invoice_paid

        return mark_invoice_paid(
            ctx,
            invoice_id=str(args.get("invoice_id") or args.get("id") or ""),
            vendor=str(args.get("vendor") or args.get("vendor_name") or args.get("supplier") or ""),
            invoice_number=str(args.get("invoice_number") or args.get("number") or ""),
            method=str(args.get("method") or args.get("payment_method") or ""),
            reference=str(args.get("reference") or args.get("payment_reference") or ""),
            amount=args.get("amount"),
            paid_on=args.get("paid_on") or args.get("paid_at"),
        )

    if name == "attach_invoice_proof":
        from miya.services.ops.invoices import attach_invoice_proof

        return attach_invoice_proof(
            ctx,
            invoice_id=str(args.get("invoice_id") or args.get("id") or ""),
            vendor=str(args.get("vendor") or args.get("vendor_name") or ""),
            proof_url=str(args.get("proof_url") or args.get("url") or args.get("photo_url") or ""),
            mark_paid=args.get("mark_paid", False) in (True, "true", "1", 1, "yes"),
        )

    if name == "return_invoice":
        from miya.services.ops.invoices import return_invoice

        return return_invoice(
            ctx,
            invoice_id=str(args.get("invoice_id") or args.get("id") or ""),
            vendor=str(args.get("vendor") or args.get("vendor_name") or ""),
            reason=str(args.get("reason") or args.get("notes") or args.get("returned_reason") or ""),
        )

    if name == "retrieve_operational_history":
        from miya.services.ops.history import retrieve_operational_history

        return retrieve_operational_history(
            ctx,
            q=str(args.get("q") or args.get("query") or ""),
            days=int(args.get("days") or 7),
            limit=int(args.get("limit") or 25),
        )

    if name == "recall_operational_memory":
        from miya.services.intelligence.operational_memory import recall_operational_memory

        return recall_operational_memory(
            ctx,
            q=str(args.get("q") or args.get("query") or ""),
            entity_type=str(args.get("entity_type") or args.get("kind") or ""),
            entity_id=str(args.get("entity_id") or args.get("id") or ""),
            days=int(args.get("days") or 14),
        )

    if name == "get_event_history":
        from miya.services.intelligence.event_history import get_event_history

        return get_event_history(
            ctx,
            event_type=str(args.get("event_type") or ""),
            entity_type=str(args.get("entity_type") or args.get("kind") or ""),
            entity_id=str(args.get("entity_id") or args.get("id") or ""),
            q=str(args.get("q") or args.get("query") or ""),
            limit=int(args.get("limit") or 40),
        )

    if name == "get_entity_history":
        from miya.services.ops.history import get_entity_history

        return get_entity_history(
            ctx,
            entity_type=str(args.get("entity_type") or args.get("kind") or ""),
            entity_id=str(args.get("entity_id") or args.get("id") or ""),
            q=str(args.get("q") or args.get("query") or args.get("title") or ""),
            limit=int(args.get("limit") or 50),
        )

    if name == "get_current_entity_state":
        from miya.services.ops.history import get_current_entity_state

        return get_current_entity_state(
            ctx,
            entity_type=str(args.get("entity_type") or args.get("kind") or ""),
            entity_id=str(args.get("entity_id") or args.get("id") or ""),
            q=str(args.get("q") or args.get("query") or args.get("title") or ""),
        )

    if name in ("create_ops_task", "create_dashboard_task"):
        # Ambiguous create without assignee → clarify (unless assign_to_category)
        from miya.services.ops.tasks import create_task
        from miya.services.working_set import looks_like_pronoun_ref

        assignee_name = str(
            args.get("assignee_name") or args.get("staff_name") or args.get("name") or ""
        ).strip()
        assignee_id = str(args.get("assignee_id") or "").strip()
        if args.get("assign_to_category") and not assignee_name and not assignee_id:
            from miya.services.ops.categories import route_responsibility_event

            return route_responsibility_event(
                ctx,
                category=str(args.get("assign_to_category") or args.get("category") or ""),
                kind="task",
                title=str(args.get("title") or args.get("task_title") or "").strip(),
                create_task=True,
                task_description=str(args.get("description") or args.get("source_text") or ""),
                location_id=str(args.get("location_id") or ctx.location_id or ""),
                notify=True,
            )
        if looks_like_pronoun_ref(assignee_name):
            return fail(
                code="needs_clarification",
                message="Who should I assign this to? Give me a staff name.",
                needs_clarification=True,
            )
        return create_task(
            ctx,
            title=str(args.get("title") or args.get("task_title") or "").strip(),
            assignee_name=assignee_name,
            assignee_id=assignee_id,
            description=str(args.get("description") or ""),
            priority=str(args.get("priority") or "MEDIUM"),
            category=str(args.get("category") or ""),
            source_text=str(args.get("source_text") or args.get("user_message") or ""),
        )

    if name in ("assign_ops_task", "reassign_dashboard_task"):
        from miya.services.ops.tasks import assign_task
        from miya.services.working_set import looks_like_pronoun_ref

        if args.get("_ambiguous_task_candidates"):
            return fail(
                code="needs_clarification",
                message="Several recent tasks could be 'it' — which one should I assign?",
                needs_clarification=True,
                data={"candidates": args.get("_ambiguous_task_candidates")},
            )

        task_id = str(args.get("task_id") or args.get("task_ref") or args.get("id") or "").strip()
        q = str(args.get("q") or args.get("title") or args.get("task_title") or "").strip()
        if looks_like_pronoun_ref(task_id):
            task_id = ""
        task_id, q = _normalize_task_lookup_args(task_id, q, title=str(args.get("title") or ""))
        if not task_id and not q:
            return fail(
                code="needs_clarification",
                message="Which task should I assign? Tell me the title or short ref — I won't guess.",
                needs_clarification=True,
            )
        return assign_task(
            ctx,
            assignee_name=str(args.get("assignee_name") or args.get("staff_name") or args.get("name") or ""),
            assignee_id=str(args.get("assignee_id") or ""),
            task_id=task_id,
            q=q,
            title=str(args.get("title") or ""),
        )

    if name in ("update_ops_task_status", "update_dashboard_task_status"):
        from miya.services.ops.tasks import update_task_status
        from miya.services.working_set import looks_like_pronoun_ref

        task_id = str(args.get("task_id") or args.get("task_ref") or args.get("id") or "").strip()
        q = str(args.get("q") or args.get("title") or args.get("task_title") or "").strip()
        if looks_like_pronoun_ref(task_id):
            task_id = ""
        task_id, q = _normalize_task_lookup_args(task_id, q, title=str(args.get("title") or ""))
        if not task_id and not q:
            return fail(
                code="needs_clarification",
                message="Which task should I update? Give me the title or short ref.",
                needs_clarification=True,
            )
        return update_task_status(
            ctx,
            status=str(args.get("status") or args.get("new_status") or ""),
            task_id=task_id,
            q=q,
            title=str(args.get("title") or ""),
            assignee_scope=ctx.channel == "whatsapp",
            notify_managers=ctx.channel == "whatsapp",
            operation_id=str(args.get("_operation_id") or args.get("operation_id") or ""),
        )

    if name == "update_dashboard_task":
        from miya.services.ops.tasks import update_task
        from miya.services.working_set import looks_like_pronoun_ref

        task_id = str(args.get("task_id") or args.get("task_ref") or args.get("id") or "").strip()
        q = str(args.get("q") or args.get("title") or args.get("task_title") or "").strip()
        if looks_like_pronoun_ref(task_id):
            task_id = ""
        task_id, q = _normalize_task_lookup_args(task_id, q, title=str(args.get("title") or ""))
        if not task_id and not q:
            return fail(
                code="needs_clarification",
                message="Which task should I update? Give me the title or short ref.",
                needs_clarification=True,
            )
        return update_task(
            ctx,
            task_id=task_id,
            q=q,
            title=str(args.get("title") or ""),
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

    # Phase 11 Wave 1 — canonical legacy mutation handlers
    from miya.services.ops import wave1_mutations as w1

    _WAVE1_DISPATCH: dict[str, Any] = {
        "staff_clock_in": w1.staff_clock_in,
        "staff_clock_out": w1.staff_clock_out,
        "staff_request": w1.staff_request,
        "approve_staff_request": w1.approve_staff_request,
        "reject_staff_request": w1.reject_staff_request,
        "request_time_off": w1.request_time_off,
        "create_shift": w1.create_shift,
        "assign_coverage": w1.assign_coverage,
        "mark_no_show": w1.mark_no_show,
        "assign_invoice": w1.assign_invoice,
        "send_announcement": w1.send_announcement,
        "notify_manager_urgent": w1.notify_manager_urgent,
        "chase_operational_record": w1.chase_operational_record,
        "report_waste": w1.report_waste,
        "update_compliance_document": w1.update_compliance_document,
        "recognize_staff": w1.recognize_staff,
    }
    if name in _WAVE1_DISPATCH:
        handler = _WAVE1_DISPATCH[name]
        return handler(ctx, **args)

    return None
