"""
Operational Memory — authoritative observations from Mizan DB + durable events.

This is the most important memory layer after live database reads.
"""
from __future__ import annotations

import logging
from typing import Any

from miya.services.ops.context import OpsContext
from miya.services.ops.result import OpsResult, fail, ok

logger = logging.getLogger("miya.intelligence.operational_memory")

# Canonical event types (Phase 2)
TASK_CREATED = "TASK_CREATED"
TASK_COMPLETED = "TASK_COMPLETED"
TASK_ASSIGNED = "TASK_ASSIGNED"
TASK_STATUS_CHANGED = "TASK_STATUS_CHANGED"
INCIDENT_CREATED = "INCIDENT_CREATED"
INCIDENT_ROUTED = "INCIDENT_ROUTED"
INCIDENT_RESOLVED = "INCIDENT_RESOLVED"
DOCUMENT_UPLOADED = "DOCUMENT_UPLOADED"
DOCUMENT_EXPIRING = "DOCUMENT_EXPIRING"
INVOICE_APPROVED = "INVOICE_APPROVED"
INVOICE_PAID = "INVOICE_PAID"
INVOICE_SUBMITTED = "INVOICE_SUBMITTED"
MEETING_CREATED = "MEETING_CREATED"
REMINDER_CREATED = "REMINDER_CREATED"

ACTION_TO_EVENT: dict[str, str] = {
    "create_task": TASK_CREATED,
    "complete_task": TASK_COMPLETED,
    "assign_task": TASK_ASSIGNED,
    "update_task_status": TASK_STATUS_CHANGED,
    "create_incident": INCIDENT_CREATED,
    "assign_incident": INCIDENT_ROUTED,
    "resolve_incident": INCIDENT_RESOLVED,
    "retrieve_document": DOCUMENT_UPLOADED,  # only when upload path emits; see normalize
    "submit_invoice": INVOICE_SUBMITTED,
    "approve_invoice": INVOICE_APPROVED,
    "mark_invoice_paid": INVOICE_PAID,
    "create_meeting": MEETING_CREATED,
    "create_reminder": REMINDER_CREATED,
}


def normalize_event_type(
    *,
    event_type: str = "",
    operation: str = "",
    payload: dict[str, Any] | None = None,
) -> str:
    raw = (event_type or "").strip()
    if raw and raw.upper() == raw and "_" in raw:
        return raw
    op = (operation or "").strip()
    mapped = ACTION_TO_EVENT.get(op)
    if mapped == TASK_STATUS_CHANGED and payload:
        new_status = str(payload.get("new_status") or "").upper()
        if new_status == "COMPLETED":
            return TASK_COMPLETED
    if mapped:
        return mapped
    # action.verified style
    if "." in raw:
        base = raw.split(".", 1)[0]
        return ACTION_TO_EVENT.get(base, raw.upper().replace(".", "_"))
    return (raw or op or "OPS_EVENT").upper()


def record_operational_observation(
    *,
    restaurant,
    event_type: str,
    entity_type: str = "",
    entity_id: str = "",
    entity_label: str = "",
    summary: str = "",
    payload: dict[str, Any] | None = None,
    actor=None,
    location_id: str = "",
    channel: str = "",
    operation_id: str = "",
    message_id: str = "",
    conversation_id: str = "",
) -> dict[str, Any] | None:
    """Persist a durable operational observation (survives restart)."""
    if not restaurant:
        return None
    try:
        import uuid as _uuid

        _uuid.UUID(str(getattr(restaurant, "id", "") or ""))
    except Exception:
        return None
    try:
        from miya.models import OperationalEvent

        loc = None
        if location_id:
            try:
                from accounts.models import BusinessLocation

                loc = BusinessLocation.objects.filter(
                    id=location_id, restaurant=restaurant
                ).first()
            except Exception:
                loc = None
        row = OperationalEvent.objects.create(
            restaurant=restaurant,
            location=loc,
            actor=actor if getattr(actor, "pk", None) else None,
            event_type=event_type[:64],
            entity_type=(entity_type or "")[:32],
            entity_id=str(entity_id or "")[:64],
            entity_label=(entity_label or "")[:255],
            summary=(summary or "")[:512],
            payload=payload or {},
            channel=(channel or "")[:32],
            operation_id=(operation_id or "")[:128],
            message_id=(message_id or "")[:128],
            conversation_id=(conversation_id or "")[:128],
        )
        return {
            "id": str(row.id),
            "event_type": row.event_type,
            "entity_type": row.entity_type,
            "entity_id": row.entity_id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
    except Exception:
        logger.exception("record_operational_observation failed")
        return None


def list_operational_events(
    ctx: OpsContext,
    *,
    entity_type: str = "",
    entity_id: str = "",
    event_type: str = "",
    q: str = "",
    limit: int = 40,
) -> OpsResult:
    err = _require(ctx)
    if err:
        return err
    from django.db.models import Q
    from miya.models import OperationalEvent

    qs = OperationalEvent.objects.filter(restaurant=ctx.restaurant)
    if ctx.location_id:
        qs = qs.filter(Q(location_id=ctx.location_id) | Q(location__isnull=True))
    if entity_type:
        qs = qs.filter(entity_type__iexact=entity_type)
    if entity_id:
        qs = qs.filter(entity_id=str(entity_id))
    if event_type:
        qs = qs.filter(event_type=event_type)
    needle = (q or "").strip()
    if needle:
        qs = qs.filter(
            Q(summary__icontains=needle)
            | Q(entity_label__icontains=needle)
            | Q(entity_id__icontains=needle)
            | Q(event_type__icontains=needle)
        )
    rows = [
        {
            "id": str(e.id),
            "event_type": e.event_type,
            "entity_type": e.entity_type,
            "entity_id": e.entity_id,
            "entity_label": e.entity_label,
            "summary": e.summary,
            "channel": e.channel,
            "payload": e.payload,
            "created_at": e.created_at.isoformat() if e.created_at else None,
            "source": "operational_event",
        }
        for e in qs.order_by("-created_at")[: max(1, min(int(limit or 40), 100))]
    ]
    return ok(
        message=f"Found {len(rows)} operational event(s).",
        verified=True,
        data={
            "layer": "STRUCTURED_OPERATIONAL_MEMORY",
            "events": rows,
            "count": len(rows),
            "overrides_conversation_memory": True,
        },
    )


def reconstruct_entity_timeline(
    ctx: OpsContext,
    *,
    entity_type: str = "",
    entity_id: str = "",
    q: str = "",
) -> OpsResult:
    """
    Reconstruct operational history for an entity from DB state + event log.

    Example: "What happened with the freezer incident?"
    """
    err = _require(ctx)
    if err:
        return err

    et = (entity_type or "").strip().lower()
    eid = (entity_id or "").strip()
    needle = (q or "").strip()
    current: dict[str, Any] | None = None
    photos: list = []

    if et in ("", "incident") and (eid or needle):
        from miya.services.intelligence.reality import get_current_incident

        cur = get_current_incident(ctx, incident_id=eid, q=needle if not eid else "")
        if cur.success:
            current = (cur.data or {}).get("incident")
            photos = (cur.data or {}).get("photos") or []
            eid = str((current or {}).get("id") or eid)
            et = "incident"
        elif et == "incident" and not cur.needs_clarification:
            pass
        elif cur.needs_clarification:
            return cur

    if et in ("", "task") and current is None and (eid or needle):
        from miya.services.intelligence.reality import get_current_task

        cur = get_current_task(ctx, task_id=eid, q=needle if not eid else "")
        if cur.success:
            current = (cur.data or {}).get("task")
            eid = str((current or {}).get("id") or eid)
            et = "task"
        elif cur.needs_clarification:
            return cur

    events_result = list_operational_events(
        ctx, entity_type=et, entity_id=eid, q="" if eid else needle, limit=50
    )
    events = (events_result.data or {}).get("events") or []

    if not current and not events:
        # Fall back to broad operational history search
        from miya.services.ops.history import retrieve_operational_history

        hist = retrieve_operational_history(ctx, q=needle or eid, days=30, limit=20)
        if not hist.success:
            return fail(
                code="timeline_empty",
                message="I couldn't reconstruct that operational history from the database.",
                data={"layer": "STRUCTURED_OPERATIONAL_MEMORY"},
            )
        return ok(
            message=hist.message_for_user,
            verified=True,
            data={
                "layer": "STRUCTURED_OPERATIONAL_MEMORY",
                "current": None,
                "timeline": (hist.data or {}).get("matches") or [],
                "events": [],
                "source": "database_history",
            },
        )

    timeline = []
    if current:
        timeline.append(
            {
                "kind": "current_state",
                "authority": "CURRENT_DATABASE_STATE",
                "entity": current,
            }
        )
    for ev in reversed(events):  # chronological
        timeline.append(
            {
                "kind": "event",
                "authority": "RECENT_OPERATIONAL_EVENT",
                **ev,
            }
        )

    label = (current or {}).get("title") or (current or {}).get("entity_label") or needle or eid
    return ok(
        message=f"Reconstructed timeline for {et} *{label}* ({len(events)} event(s)).",
        verified=True,
        data={
            "layer": "STRUCTURED_OPERATIONAL_MEMORY",
            "entity_type": et,
            "entity_id": eid,
            "current": current,
            "photos": photos,
            "events": events,
            "timeline": timeline,
            "overrides_conversation_memory": True,
            "source": "database+events",
        },
        miya_directive=(
            "Answer from current + timeline only. "
            "Current database state wins over older events if they conflict."
        ),
    )


def recall_operational_memory(
    ctx: OpsContext,
    *,
    q: str = "",
    entity_type: str = "",
    entity_id: str = "",
    days: int = 14,
) -> OpsResult:
    """Unified recall: live DB entities + durable events."""
    if entity_id or (q and entity_type in ("incident", "task", "")):
        timeline = reconstruct_entity_timeline(
            ctx, entity_type=entity_type, entity_id=entity_id, q=q
        )
        if timeline.success or timeline.needs_clarification:
            return timeline

    from miya.services.ops.history import retrieve_operational_history

    hist = retrieve_operational_history(ctx, q=q, days=days, limit=25)
    events = list_operational_events(ctx, q=q, entity_type=entity_type, limit=25)
    merged_events = (events.data or {}).get("events") or []
    matches = (hist.data or {}).get("matches") or [] if hist.success else []
    if not matches and not merged_events:
        return fail(
            code="operational_memory_empty",
            message="Nothing matched in operational memory.",
            data={"layer": "STRUCTURED_OPERATIONAL_MEMORY"},
        )
    return ok(
        message=f"Operational memory: {len(matches)} record(s), {len(merged_events)} event(s).",
        verified=True,
        data={
            "layer": "STRUCTURED_OPERATIONAL_MEMORY",
            "records": matches,
            "events": merged_events,
            "count": len(matches) + len(merged_events),
            "overrides_conversation_memory": True,
            "source": "database+events",
        },
    )


def _require(ctx: OpsContext):
    from miya.services.ops.context import require_restaurant

    return require_restaurant(ctx)
