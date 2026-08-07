"""Event layer — emit structured ops events + persist operational memory."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("miya.intelligence.events")

_ops_signal = None


def get_ops_signal():
    global _ops_signal
    if _ops_signal is None:
        from django.dispatch import Signal

        _ops_signal = Signal()
    return _ops_signal


def emit_ops_event(
    *,
    event_type: str,
    operation: str,
    execution_context: dict[str, Any] | None = None,
    entity_type: str = "",
    entity_id: str = "",
    payload: dict[str, Any] | None = None,
    success: bool = True,
    entity_label: str = "",
    summary: str = "",
    restaurant=None,
    actor=None,
) -> dict[str, Any]:
    """
    Emit an operational event for notifications / audit / durable memory.

    Persists to OperationalEvent (survives restart) when restaurant is known.
    """
    from miya.services.intelligence.operational_memory import (
        normalize_event_type,
        record_operational_observation,
    )

    ctx = execution_context or {}
    payload_safe = _safe_payload(payload)
    canonical = normalize_event_type(
        event_type=event_type, operation=operation, payload=payload_safe
    )
    label = entity_label or str(
        payload_safe.get("title")
        or (payload_safe.get("task") or {}).get("title")
        or (payload_safe.get("incident") or {}).get("title")
        or ""
    )
    text = summary or _default_summary(canonical, entity_type, entity_id, label, payload_safe)

    event = {
        "event_type": canonical,
        "operation": operation,
        "success": success,
        "entity_type": entity_type or None,
        "entity_id": entity_id or None,
        "entity_label": label or None,
        "summary": text,
        "message_id": ctx.get("message_id"),
        "conversation_id": ctx.get("conversation_id"),
        "user_id": ctx.get("user_id"),
        "organization_id": ctx.get("organization_id"),
        "establishment_id": ctx.get("establishment_id"),
        "channel": ctx.get("channel"),
        "payload": payload_safe,
    }
    logger.info("MIYA_OPS_EVENT %s", event)

    # Resolve restaurant for durable write
    rest = restaurant
    if rest is None and ctx.get("organization_id"):
        try:
            from accounts.models import Restaurant

            rest = Restaurant.objects.filter(id=ctx["organization_id"]).first()
        except Exception:
            rest = None

    if success and rest is not None:
        record_operational_observation(
            restaurant=rest,
            event_type=canonical,
            entity_type=entity_type or "",
            entity_id=entity_id or "",
            entity_label=label,
            summary=text,
            payload=payload_safe,
            actor=actor,
            location_id=str(ctx.get("establishment_id") or ""),
            channel=str(ctx.get("channel") or ""),
            operation_id=str(payload_safe.get("operation_id") or ""),
            message_id=str(ctx.get("message_id") or ""),
            conversation_id=str(ctx.get("conversation_id") or ""),
        )
        # Refresh working-memory pointers (ids/labels only)
        if actor is not None and entity_id:
            try:
                from miya.services.intelligence.working_memory import touch_from_entity

                touch_from_entity(
                    user=actor,
                    restaurant=rest,
                    entity_type=entity_type or "",
                    entity_id=str(entity_id),
                    entity_label=label,
                    establishment_id=str(ctx.get("establishment_id") or ""),
                    establishment_name=str(ctx.get("establishment_name") or ""),
                    workflow=operation or "",
                )
            except Exception:
                logger.exception("working memory touch failed")

    try:
        get_ops_signal().send(sender=None, event=event)
    except Exception:
        pass
    return event


def _default_summary(
    event_type: str,
    entity_type: str,
    entity_id: str,
    label: str,
    payload: dict[str, Any],
) -> str:
    name = label or entity_id or "item"
    if event_type == "TASK_COMPLETED":
        return f"Task {name} completed."
    if event_type == "TASK_ASSIGNED":
        return f"Task {name} assigned."
    if event_type == "TASK_STATUS_CHANGED":
        prev = payload.get("previous_status")
        new = payload.get("new_status")
        return f"Task {name} status {prev} → {new}."
    if event_type == "INCIDENT_CREATED":
        return f"Incident {name} created."
    if event_type == "INCIDENT_ROUTED":
        return f"Incident {name} routed."
    if event_type == "INCIDENT_RESOLVED":
        return f"Incident {name} resolved."
    if event_type == "INVOICE_APPROVED":
        return f"Invoice {name} approved."
    if event_type == "INVOICE_PAID":
        return f"Invoice {name} marked paid."
    if event_type == "REMINDER_CREATED":
        return f"Reminder {name} created."
    return f"{event_type}: {entity_type} {name}".strip()


def _safe_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {}
    blocked = {
        "password",
        "token",
        "access_token",
        "authorization",
        "ssn",
        "card_number",
        "cvv",
    }
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if str(k).lower() in blocked:
            continue
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, dict):
            out[k] = {sk: sv for sk, sv in v.items() if str(sk).lower() not in blocked}
        elif isinstance(v, list) and len(v) <= 20:
            out[k] = v
        else:
            out[k] = str(v)[:200]
    return out
