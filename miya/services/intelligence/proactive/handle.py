"""Act on briefing follow-ups — same structured actions as every channel."""
from __future__ import annotations

import logging
import re
from typing import Any

from miya.services.intelligence.actions import execute_structured_action
from miya.services.intelligence.proactive.briefing import category_from_handle_phrase
from miya.services.intelligence.proactive.dedupe import load_briefing_context
from miya.services.intelligence.proactive.types import AttentionCategory, AttentionItem
from miya.services.intelligence.unified import ops_context_for_channel
from miya.services.ops.result import OpsResult, clarify, fail, ok

logger = logging.getLogger("miya.intelligence.proactive.handle")


def try_handle_briefing_request(
    *,
    user,
    message: str,
    channel: str = "whatsapp",
    restaurant=None,
) -> dict[str, Any] | None:
    """
    If the user says "Handle the invoices." (etc.), identify entities from the
    last briefing / live scan and begin the appropriate workflow.
    Returns a chat-shaped dict, or None if this isn't a handle request.
    """
    cat = category_from_handle_phrase(message)
    if cat is None:
        return None

    restaurant = restaurant or getattr(user, "restaurant", None)
    rid = str(getattr(restaurant, "id", None) or getattr(user, "restaurant_id", None) or "")
    brief = load_briefing_context(rid, str(getattr(user, "id", "") or "")) if rid else None

    if not _looks_like_handle(message):
        # Only accept bare domain words when a recent briefing exists
        if brief is None:
            return None

    if restaurant is None:
        return {
            "reply": "I need your workspace linked before I can handle that.",
            "success": False,
            "presentation_only": True,
        }

    item = _resolve_item(user, restaurant, cat, brief)
    if item is None or item.count == 0:
        return {
            "reply": f"I don't see any {cat.value.replace('_', ' ')} that need handling right now.",
            "success": True,
            "presentation_only": True,
            "proactive_handle": cat.value,
        }

    result = _run_workflow(user, restaurant, channel, cat, item)
    return {
        "reply": result.message_for_user,
        "success": result.success,
        "verified": result.verified,
        "needs_clarification": result.needs_clarification,
        "tool_trace": [
            {
                "tool": "proactive_handle",
                "category": cat.value,
                "result": result.as_tool_response(),
            }
        ],
        "presentation_only": True,
        "proactive_handle": cat.value,
        "assistant_text_is_not_executable": True,
    }


def _looks_like_handle(message: str) -> bool:
    return bool(
        re.search(
            r"\b(handle|take care of|deal with|process|sort(?:\s+out)?)\b",
            message or "",
            re.I,
        )
    )


def _resolve_item(user, restaurant, cat: AttentionCategory, brief) -> AttentionItem | None:
    if brief:
        for item in brief.items:
            if item.category == cat:
                return item
    from miya.services.intelligence.proactive.scanner import scan_daily_operations

    live = scan_daily_operations(restaurant, user=user, period="morning")
    for item in live.items:
        if item.category == cat:
            return item
    return None


def _run_workflow(
    user,
    restaurant,
    channel: str,
    cat: AttentionCategory,
    item: AttentionItem,
) -> OpsResult:
    ctx = ops_context_for_channel(user=user, channel=channel, restaurant=restaurant)
    if ctx is None:
        return fail(code="no_workspace", message="Workspace not linked.")

    exec_ctx = {
        "user_id": str(user.id),
        "organization_id": str(restaurant.id),
        "channel": channel,
        "message_id": f"proactive:handle:{cat.value}:{user.id}",
    }

    if cat in (AttentionCategory.PENDING_APPROVALS, AttentionCategory.PAYMENT_ISSUES):
        return _handle_invoices(ctx, exec_ctx, item)
    if cat == AttentionCategory.OPEN_INCIDENTS:
        return _handle_incidents(ctx, exec_ctx, item)
    if cat == AttentionCategory.OVERDUE_TASKS:
        return ok(
            message=(
                f"I see {item.count} overdue task(s)"
                + (f": {item.detail}" if item.detail else "")
                + ". Say *follow up on overdue tasks* to nudge assignees, "
                "or name a task to reassign/complete."
            ),
            verified=True,
            data={"task_ids": item.entity_ids},
        )
    if cat == AttentionCategory.EXPIRING_DOCUMENTS:
        args = {"q": "insurance"}
        if item.entity_ids:
            args["document_id"] = item.entity_ids[0]
        return execute_structured_action(
            "sync_compliance_reminder",
            args,
            ctx=ctx,
            execution_context=exec_ctx,
            intent="REMIND",
        )
    if cat == AttentionCategory.BLOCKED_TASKS:
        return clarify(
            message=(
                f"I found {item.count} blocked task(s): {item.detail or item.title}. "
                "Tell me which to reassign or cancel — I won't guess."
            ),
            data={"items": item.to_dict()},
        )
    if cat == AttentionCategory.UNCOMPLETED_CHECKLISTS:
        return ok(
            message=(
                f"{item.title}. I can nudge assignees on WhatsApp — "
                "say *nudge checklists* to send reminders."
            ),
            verified=True,
            data={"items": item.to_dict()},
        )
    return ok(
        message=f"{item.title}. Tell me what you'd like me to do next.",
        verified=True,
        data={"items": item.to_dict()},
    )


def _handle_invoices(ctx, exec_ctx, item: AttentionItem) -> OpsResult:
    ids = [i for i in item.entity_ids if i]
    if not ids:
        return fail(code="none", message="I couldn't identify which invoices to handle.")

    if len(ids) > 3:
        return clarify(
            message=(
                f"I found {len(ids)} invoices awaiting approval"
                + (f" ({item.detail})" if item.detail else "")
                + ". Shall I submit them for PayGuard approval? "
                "Reply *yes, approve all* or name a vendor."
            ),
            data={"invoice_ids": ids, "awaiting_confirm": True},
        )

    traces = []
    last: OpsResult | None = None
    for iid in ids[:3]:
        last = execute_structured_action(
            "submit_invoice",
            {"invoice_id": iid},
            ctx=ctx,
            execution_context=exec_ctx,
            intent="APPROVE",
        )
        traces.append(last.as_tool_response())
        if last.needs_clarification:
            break

    assert last is not None
    msg = last.message_for_user
    if len(ids) > 1 and last.success:
        msg = f"Started approval workflow for {min(len(ids), 3)} invoice(s). {msg}"
    return ok(
        message=msg,
        verified=bool(last.verified),
        data={"invoice_ids": ids, "traces": traces},
    )


def _handle_incidents(ctx, exec_ctx, item: AttentionItem) -> OpsResult:
    ids = [i for i in item.entity_ids if i]
    if not ids:
        return fail(message="I couldn't identify open incidents.")
    if len(ids) > 1:
        return clarify(
            message=(
                f"There are {len(ids)} open incidents"
                + (f": {item.detail}" if item.detail else "")
                + ". Which should I route or escalate? Reply with the title — I won't guess."
            ),
            data={"incident_ids": ids},
        )
    return execute_structured_action(
        "assign_incident",
        {"incident_id": ids[0]},
        ctx=ctx,
        execution_context=exec_ctx,
        intent="ROUTE",
    )
