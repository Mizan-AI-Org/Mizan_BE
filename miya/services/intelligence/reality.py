"""
Current Reality — retrieve live database state.

Conversation memory must never override these results.
"""
from __future__ import annotations

from typing import Any

from miya.services.ops.context import OpsContext
from miya.services.ops.result import OpsResult, fail, ok


def get_current_task(
    ctx: OpsContext,
    *,
    task_id: str = "",
    q: str = "",
    title: str = "",
    assignee_name: str = "",
    mine_only: bool = False,
) -> OpsResult:
    from accounts.rbac_enforce import miya_has_full_tenant_access, user_can_action
    from miya.services.ops.tasks import get_task_state

    if not mine_only and not assignee_name:
        if not miya_has_full_tenant_access(ctx.user, ctx.restaurant) and not user_can_action(
            ctx.user, "manage_widgets", restaurant=ctx.restaurant
        ):
            mine_only = True

    result = get_task_state(
        ctx,
        task_id=task_id,
        q=q,
        title=title,
        assignee_name=assignee_name,
        mine_only=mine_only,
    )
    return _tag(result, "get_current_task")


def get_current_incident(
    ctx: OpsContext,
    *,
    incident_id: str = "",
    q: str = "",
) -> OpsResult:
    from miya.services.ops.incidents import get_incident

    result = get_incident(ctx, incident_id=incident_id, q=q)
    return _tag(result, "get_current_incident")


def get_current_staff(
    ctx: OpsContext,
    *,
    name: str = "",
    role: str = "",
    q: str = "",
    limit: int = 20,
) -> OpsResult:
    from miya.services.ops.staff import find_staff

    result = find_staff(ctx, name=name, role=role, q=q, limit=limit)
    return _tag(result, "get_current_staff")


def get_current_establishment(
    ctx: OpsContext,
    *,
    q: str = "",
    location_id: str = "",
) -> OpsResult:
    from miya.services.ops.establishments import find_establishments

    if location_id or ctx.location_id:
        lid = (location_id or ctx.location_id or "").strip()
        for row in ctx.available_locations or []:
            if str(row.get("id")) == lid:
                return ok(
                    message=f"Active establishment: *{row.get('name')}*.",
                    verified=True,
                    data={
                        "operation": "get_current_establishment",
                        "establishment": row,
                        "source": "database",
                    },
                )
        return fail(
            code="establishment_not_found",
            message="I couldn't find that establishment in your access list.",
            data={"operation": "get_current_establishment"},
        )
    result = find_establishments(ctx, q=q)
    return _tag(result, "get_current_establishment")


def get_current_assignment(
    ctx: OpsContext,
    *,
    category: str = "",
    q: str = "",
) -> OpsResult:
    from miya.services.ops.categories import find_category_owners

    needle = (category or q or "").strip()
    if not needle:
        return fail(
            code="category_required",
            message="Which category or responsibility should I look up?",
            data={"operation": "get_current_assignment"},
        )
    result = find_category_owners(ctx, category=needle, q=needle)
    return _tag(result, "get_current_assignment")


def get_current_document(
    ctx: OpsContext,
    *,
    document_id: str = "",
    q: str = "",
) -> OpsResult:
    from miya.services.ops.documents import get_document

    result = get_document(ctx, document_id=document_id, q=q)
    return _tag(result, "get_current_document")


def get_current_invoice(
    ctx: OpsContext,
    *,
    invoice_id: str = "",
    q: str = "",
) -> OpsResult:
    from miya.services.ops.invoices import get_invoice

    result = get_invoice(ctx, invoice_id=invoice_id, q=q)
    return _tag(result, "get_current_invoice")


def get_current_reminder(
    ctx: OpsContext,
    *,
    reminder_id: str = "",
    q: str = "",
) -> OpsResult:
    from miya.services.ops.meetings import list_reminders

    result = list_reminders(ctx, q=q or reminder_id)
    if result.success:
        rows = (result.data or {}).get("reminders") or (result.data or {}).get("items") or []
        if reminder_id:
            match = [r for r in rows if str(r.get("id")) == str(reminder_id)]
            if len(match) == 1:
                return ok(
                    message=f"Reminder *{match[0].get('title')}* is on file.",
                    verified=True,
                    data={
                        "operation": "get_current_reminder",
                        "reminder": match[0],
                        "source": "database",
                    },
                )
        if len(rows) == 1:
            return ok(
                message=f"Reminder *{rows[0].get('title')}* is on file.",
                verified=True,
                data={
                    "operation": "get_current_reminder",
                    "reminder": rows[0],
                    "reminders": rows,
                    "source": "database",
                },
            )
    return _tag(result, "get_current_reminder")


def get_current_meeting(
    ctx: OpsContext,
    *,
    meeting_id: str = "",
    q: str = "",
) -> OpsResult:
    from miya.services.ops.meetings import list_meetings

    result = list_meetings(ctx, q=q or meeting_id)
    return _tag(result, "get_current_meeting")


def _tag(result: OpsResult, operation: str) -> OpsResult:
    data = dict(result.data or {})
    data.setdefault("operation", operation)
    data.setdefault("source", "database")
    data.setdefault("overrides_conversation_memory", True)
    return OpsResult(
        success=result.success,
        code=result.code,
        message_for_user=result.message_for_user,
        miya_directive=result.miya_directive,
        data=data,
        verified=result.verified,
        needs_clarification=result.needs_clarification,
    )
