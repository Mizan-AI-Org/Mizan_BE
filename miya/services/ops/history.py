"""Retrieve operational history across tasks, incidents, staff requests."""
from __future__ import annotations

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from miya.services.ops.context import OpsContext, guard_entity_location, require_permission, require_restaurant
from miya.services.ops.result import OpsResult, fail, ok


def get_entity_history(
    ctx: OpsContext,
    *,
    entity_type: str = "",
    entity_id: str = "",
    q: str = "",
    limit: int = 50,
) -> OpsResult:
    """
    Canonical Mizan entity history — current state + chronological audit events.

    Enforces tenant isolation, establishment scoping, and RBAC.
    Miya must use this (not conversation memory) for "what happened" questions.
    """
    err = require_restaurant(ctx)
    if err:
        return err

    et = (entity_type or "").strip().lower()
    eid = (entity_id or "").strip()
    needle = (q or "").strip()

    # Invoice timeline has dedicated rich audit — delegate when resolved
    if et == "invoice" and eid:
        from miya.services.ops.invoices import get_invoice_timeline

        return get_invoice_timeline(ctx, invoice_id=eid)

    if et == "invoice" and needle:
        from miya.services.ops.invoices import get_invoice_timeline

        return get_invoice_timeline(ctx, vendor=needle, q=needle)

    # Entity resolution + timeline reconstruction
    from miya.services.intelligence.operational_memory import reconstruct_entity_timeline

    timeline = reconstruct_entity_timeline(
        ctx, entity_type=et, entity_id=eid, q=needle if not eid else ""
    )
    if timeline.needs_clarification:
        return timeline
    if not timeline.success:
        return timeline

    data = timeline.data or {}
    current = data.get("current")
    events = (data.get("events") or [])[:limit]

    # RBAC: staff may only read history for entities they can access
    if current and isinstance(current, dict) and current.get("id"):
        try:
            if et in ("", "task"):
                from dashboard.models import Task
                from miya.services.ops.context import user_can_read_task

                task = Task.objects.filter(
                    id=str(current["id"]), restaurant_id=ctx.restaurant_id
                ).first()
                if task:
                    loc_err = guard_entity_location(ctx, task)
                    if loc_err:
                        return loc_err
                    if not user_can_read_task(ctx, task):
                        return fail(
                            code="permission_denied",
                            message="You can't view history for that task.",
                        )
            elif et in ("", "incident"):
                from staff.models_task import SafetyConcernReport

                inc = SafetyConcernReport.objects.filter(
                    id=str(current["id"]), restaurant_id=ctx.restaurant_id
                ).first()
                if inc:
                    loc_err = guard_entity_location(ctx, inc)
                    if loc_err:
                        return loc_err
        except Exception:
            pass

    is_historical_question = bool(needle and any(
        p in needle.lower()
        for p in ("what happened", "who changed", "who reassigned", "when was", "history")
    ))

    return ok(
        message=timeline.message_for_user,
        verified=True,
        data={
            "layer": "CANONICAL_ENTITY_HISTORY",
            "entity_type": data.get("entity_type") or et,
            "entity_id": data.get("entity_id") or eid,
            "current_state": current,
            "history": events,
            "timeline": data.get("timeline") or [],
            "photos": data.get("photos") or [],
            "count": len(events),
            "answer_mode": "historical" if is_historical_question else "current_and_history",
            "overrides_conversation_memory": True,
            "source": "database+operational_events",
        },
        miya_directive=(
            "Answer historical questions from history/timeline only. "
            "For status-only questions, lead with current_state."
        ),
    )


def get_current_entity_state(
    ctx: OpsContext,
    *,
    entity_type: str = "",
    entity_id: str = "",
    q: str = "",
) -> OpsResult:
    """Current DB state only — no historical events."""
    hist = get_entity_history(ctx, entity_type=entity_type, entity_id=entity_id, q=q, limit=0)
    if not hist.success:
        return hist
    current = (hist.data or {}).get("current_state")
    if not current:
        return fail(code="not_found", message="I couldn't find that record in the database.")
    return ok(
        message=f"Current state for {entity_type or 'entity'}.",
        verified=True,
        data={
            "layer": "CURRENT_DATABASE_STATE",
            "entity_type": (hist.data or {}).get("entity_type"),
            "entity_id": (hist.data or {}).get("entity_id"),
            "current_state": current,
            "overrides_conversation_memory": True,
        },
    )


def retrieve_operational_history(
    ctx: OpsContext,
    *,
    q: str = "",
    days: int = 7,
    limit: int = 25,
) -> OpsResult:
    err = require_restaurant(ctx) or require_permission(ctx, "manage_widgets")
    if err:
        return err

    needle = (q or "").strip()
    since = timezone.now() - timedelta(days=max(1, min(int(days or 7), 90)))
    matches: list[dict] = []

    from dashboard.models import Task

    tqs = Task.objects.filter(restaurant=ctx.restaurant, updated_at__gte=since).select_related(
        "assigned_to"
    )
    if needle:
        tqs = tqs.filter(Q(title__icontains=needle) | Q(description__icontains=needle))
    for t in tqs.order_by("-updated_at")[:limit]:
        matches.append(
            {
                "kind": "task",
                "id": str(t.id),
                "title": t.title,
                "status": t.status,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            }
        )

    try:
        from staff.models_task import SafetyConcernReport
    except Exception:
        SafetyConcernReport = None

    if SafetyConcernReport is not None:
        iqs = SafetyConcernReport.objects.filter(
            restaurant=ctx.restaurant, updated_at__gte=since
        )
        if needle:
            iqs = iqs.filter(Q(title__icontains=needle) | Q(description__icontains=needle))
        for row in iqs.order_by("-updated_at")[:limit]:
            matches.append(
                {
                    "kind": "incident",
                    "id": str(row.id),
                    "title": getattr(row, "title", None) or (row.description or "")[:80],
                    "status": row.status,
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                }
            )

    try:
        from staff.models import StaffRequest

        rqs = StaffRequest.objects.filter(restaurant=ctx.restaurant, updated_at__gte=since)
        if needle:
            rqs = rqs.filter(Q(subject__icontains=needle) | Q(description__icontains=needle))
        for row in rqs.order_by("-updated_at")[:limit]:
            matches.append(
                {
                    "kind": "staff_request",
                    "id": str(row.id),
                    "title": row.subject or row.category,
                    "status": row.status,
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                }
            )
    except Exception:
        pass

    # Phase 6: invoices + tenant document uploads
    try:
        from finance.models import Invoice

        iqs = Invoice.objects.filter(restaurant=ctx.restaurant, created_at__gte=since)
        if needle:
            iqs = iqs.filter(
                Q(vendor_name__icontains=needle)
                | Q(invoice_number__icontains=needle)
                | Q(notes__icontains=needle)
            )
        for inv in iqs.order_by("-updated_at")[:limit]:
            matches.append(
                {
                    "kind": "invoice",
                    "id": str(inv.id),
                    "title": f"Invoice — {inv.vendor_name}",
                    "status": inv.status,
                    "vendor": inv.vendor_name,
                    "amount": str(inv.amount) if inv.amount is not None else None,
                    "updated_at": (inv.updated_at or inv.created_at).isoformat()
                    if (inv.updated_at or inv.created_at)
                    else None,
                }
            )
    except Exception:
        pass

    try:
        from miya.models import TenantDocument

        dqs = TenantDocument.objects.filter(restaurant=ctx.restaurant, created_at__gte=since)
        if needle:
            dqs = dqs.filter(
                Q(title__icontains=needle)
                | Q(summary__icontains=needle)
                | Q(vendor_name__icontains=needle)
                | Q(extracted_text__icontains=needle)
            )
        for doc in dqs.order_by("-created_at")[:limit]:
            matches.append(
                {
                    "kind": "tenant_document",
                    "id": str(doc.id),
                    "title": doc.title,
                    "status": doc.category,
                    "vendor": getattr(doc, "vendor_name", "") or None,
                    "amount": str(doc.amount) if getattr(doc, "amount", None) is not None else None,
                    "updated_at": doc.created_at.isoformat() if doc.created_at else None,
                }
            )
    except Exception:
        pass

    matches.sort(key=lambda m: m.get("updated_at") or "", reverse=True)
    matches = matches[:limit]
    if not matches:
        return fail(
            code="history_empty",
            message="Nothing matched in recent operational history.",
            data={"matches": [], "count": 0},
        )
    return ok(
        message=f"Found {len(matches)} recent operational record(s).",
        verified=True,
        data={"matches": matches, "count": len(matches)},
    )
