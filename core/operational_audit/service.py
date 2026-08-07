"""
Canonical operational audit — writes durable events to ``miya.OperationalEvent``.

All Mizan domain mutations (dashboard, WhatsApp, Miya, mobile, system) should
emit through this service so there is ONE operational history regardless of channel.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Task lifecycle
TASK_CREATED = "TASK_CREATED"
TASK_ASSIGNED = "TASK_ASSIGNED"
TASK_REASSIGNED = "TASK_REASSIGNED"
TASK_UPDATED = "TASK_UPDATED"
TASK_STARTED = "TASK_STARTED"
TASK_COMPLETED = "TASK_COMPLETED"
TASK_STATUS_CHANGED = "TASK_STATUS_CHANGED"
TASK_REOPENED = "TASK_REOPENED"
TASK_CANCELLED = "TASK_CANCELLED"
TASK_DELETED = "TASK_DELETED"

# Incident lifecycle
INCIDENT_CREATED = "INCIDENT_CREATED"
INCIDENT_PHOTO_ATTACHED = "INCIDENT_PHOTO_ATTACHED"
INCIDENT_ASSIGNED = "INCIDENT_ASSIGNED"
INCIDENT_ROUTED = "INCIDENT_ROUTED"
INCIDENT_STATUS_CHANGED = "INCIDENT_STATUS_CHANGED"
INCIDENT_RESOLVED = "INCIDENT_RESOLVED"
INCIDENT_ESCALATED = "INCIDENT_ESCALATED"

# Finance (mirrors InvoiceAuditEvent vocabulary)
INVOICE_CREATED = "INVOICE_CREATED"
INVOICE_ASSIGNED = "INVOICE_ASSIGNED"
INVOICE_SUBMITTED = "INVOICE_SUBMITTED"
INVOICE_APPROVED = "INVOICE_APPROVED"
INVOICE_REJECTED = "INVOICE_REJECTED"
INVOICE_RETURNED = "INVOICE_RETURNED"
INVOICE_PAID = "INVOICE_PAID"
INVOICE_PROOF_ATTACHED = "INVOICE_PROOF_ATTACHED"

# Documents / compliance
DOCUMENT_UPLOADED = "DOCUMENT_UPLOADED"
DOCUMENT_OCR_COMPLETED = "DOCUMENT_OCR_COMPLETED"
DOCUMENT_REVIEWED = "DOCUMENT_REVIEWED"
COMPLIANCE_EXPIRY_IDENTIFIED = "COMPLIANCE_EXPIRY_IDENTIFIED"
REMINDER_CREATED = "REMINDER_CREATED"
COMPLIANCE_STATUS_CHANGED = "COMPLIANCE_STATUS_CHANGED"

# Scheduling / process templates
TASK_TEMPLATE_IMPORTED = "TASK_TEMPLATE_IMPORTED"
TASK_TEMPLATE_CREATED = "TASK_TEMPLATE_CREATED"

_INVOICE_EVENT_MAP = {
    "CREATED": INVOICE_CREATED,
    "ASSIGNED": INVOICE_ASSIGNED,
    "APPROVAL_REQUESTED": INVOICE_SUBMITTED,
    "APPROVED": INVOICE_APPROVED,
    "REJECTED": INVOICE_REJECTED,
    "RETURNED": INVOICE_RETURNED,
    "PAYMENT_RECORDED": INVOICE_PAID,
    "PROOF_UPLOADED": INVOICE_PROOF_ATTACHED,
    "OCR_COMPLETED": DOCUMENT_OCR_COMPLETED,
}


def map_invoice_audit_event_type(invoice_event_type: str) -> str:
    return _INVOICE_EVENT_MAP.get((invoice_event_type or "").upper(), f"INVOICE_{invoice_event_type.upper()}")


def record_operational_audit_event(
    *,
    restaurant,
    event_type: str,
    entity_type: str,
    entity_id: str,
    actor=None,
    location_id: str = "",
    channel: str = "system",
    operation_id: str = "",
    idempotency_key: str = "",
    previous_state: dict[str, Any] | None = None,
    new_state: dict[str, Any] | None = None,
    summary: str = "",
    entity_label: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Persist exactly one operational audit event (idempotent on key).

    Returns observation dict or existing row dict when deduplicated.
    Never raises to callers.
    """
    if not restaurant or not event_type:
        return None
    try:
        from miya.models import OperationalEvent

        dedupe_key = (idempotency_key or operation_id or "").strip()
        canonical_type = (event_type or "").upper()[:64]
        eid = str(entity_id or "")[:64]
        etype = (entity_type or "")[:32]

        if dedupe_key:
            existing = OperationalEvent.objects.filter(
                restaurant=restaurant,
                operation_id=dedupe_key[:128],
                event_type=canonical_type,
                entity_type=etype,
                entity_id=eid,
            ).first()
            if existing:
                return {
                    "id": str(existing.id),
                    "event_type": existing.event_type,
                    "entity_type": existing.entity_type,
                    "entity_id": existing.entity_id,
                    "deduplicated": True,
                    "created_at": existing.created_at.isoformat() if existing.created_at else None,
                }

        payload: dict[str, Any] = dict(metadata or {})
        if previous_state is not None:
            payload["previous_state"] = previous_state
        if new_state is not None:
            payload["new_state"] = new_state
        if dedupe_key:
            payload["idempotency_key"] = dedupe_key

        from miya.services.intelligence.operational_memory import record_operational_observation

        row = record_operational_observation(
            restaurant=restaurant,
            event_type=canonical_type,
            entity_type=etype,
            entity_id=eid,
            entity_label=entity_label,
            summary=summary,
            payload=payload,
            actor=actor,
            location_id=location_id,
            channel=(channel or "system")[:32],
            operation_id=dedupe_key[:128],
        )
        if row:
            row["deduplicated"] = False
        return row
    except Exception:
        logger.exception(
            "record_operational_audit_event failed type=%s entity=%s:%s",
            event_type,
            entity_type,
            entity_id,
        )
        return None


def task_status_event_type(previous: str, new: str) -> str:
    prev = (previous or "").upper()
    nxt = (new or "").upper()
    if nxt == "COMPLETED":
        return TASK_COMPLETED
    if nxt == "CANCELLED":
        return TASK_CANCELLED
    if nxt == "IN_PROGRESS" and prev in ("PENDING", "ACCEPTED", ""):
        return TASK_STARTED
    if prev == "COMPLETED" and nxt in ("PENDING", "IN_PROGRESS", "ACCEPTED"):
        return TASK_REOPENED
    return TASK_STATUS_CHANGED
