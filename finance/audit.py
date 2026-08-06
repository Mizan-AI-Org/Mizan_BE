"""Immutable audit trail for invoice lifecycle events."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from django.db import models
from django.utils import timezone

logger = logging.getLogger(__name__)


class InvoiceAuditEvent(models.Model):
    """Append-only event log for a single invoice."""

    EVENT_CREATED = "CREATED"
    EVENT_OCR_COMPLETED = "OCR_COMPLETED"
    EVENT_DATA_EDITED = "DATA_EDITED"
    EVENT_VALIDATED = "VALIDATED"
    EVENT_APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    EVENT_APPROVAL_NOTIFIED = "APPROVAL_NOTIFIED"
    EVENT_APPROVED = "APPROVED"
    EVENT_REJECTED = "REJECTED"
    EVENT_INFO_REQUESTED = "INFO_REQUESTED"
    EVENT_RETURNED = "RETURNED"
    EVENT_PAYMENT_RECORDED = "PAYMENT_RECORDED"
    EVENT_PROOF_UPLOADED = "PROOF_UPLOADED"
    EVENT_ASSIGNED = "ASSIGNED"
    EVENT_VOIDED = "VOIDED"
    EVENT_PO_MATCHED = "PO_MATCHED"
    EVENT_NOTIFICATION_SENT = "NOTIFICATION_SENT"
    EVENT_MIYA_ACTION = "MIYA_ACTION"
    EVENT_WHATSAPP_ACTION = "WHATSAPP_ACTION"
    EVENT_COMMENT = "COMMENT"

    EVENT_CHOICES = (
        (EVENT_CREATED, "Created"),
        (EVENT_OCR_COMPLETED, "OCR completed"),
        (EVENT_DATA_EDITED, "Data edited"),
        (EVENT_VALIDATED, "Validated"),
        (EVENT_APPROVAL_REQUESTED, "Approval requested"),
        (EVENT_APPROVAL_NOTIFIED, "Approver notified"),
        (EVENT_APPROVED, "Approved"),
        (EVENT_REJECTED, "Rejected"),
        (EVENT_INFO_REQUESTED, "More information requested"),
        (EVENT_RETURNED, "Returned for correction"),
        (EVENT_PAYMENT_RECORDED, "Payment recorded"),
        (EVENT_PROOF_UPLOADED, "Proof uploaded"),
        (EVENT_ASSIGNED, "Assigned"),
        (EVENT_VOIDED, "Voided"),
        (EVENT_PO_MATCHED, "PO matched"),
        (EVENT_NOTIFICATION_SENT, "Notification sent"),
        (EVENT_MIYA_ACTION, "Miya action"),
        (EVENT_WHATSAPP_ACTION, "WhatsApp action"),
        (EVENT_COMMENT, "Comment"),
    )

    CHANNEL_DASHBOARD = "dashboard"
    CHANNEL_WHATSAPP = "whatsapp"
    CHANNEL_MIYA = "miya"
    CHANNEL_SYSTEM = "system"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(
        "finance.Invoice",
        on_delete=models.CASCADE,
        related_name="audit_events",
    )
    restaurant = models.ForeignKey(
        "accounts.Restaurant",
        on_delete=models.CASCADE,
        related_name="invoice_audit_events",
    )
    event_type = models.CharField(max_length=32, choices=EVENT_CHOICES, db_index=True)
    actor = models.ForeignKey(
        "accounts.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoice_audit_actions",
    )
    actor_label = models.CharField(max_length=120, blank=True, default="")
    channel = models.CharField(max_length=20, blank=True, default=CHANNEL_SYSTEM)
    summary = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["invoice", "created_at"]),
            models.Index(fields=["restaurant", "event_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} @ {self.created_at}"


def _actor_label(user) -> str:
    if not user:
        return ""
    name = f"{getattr(user, 'first_name', '') or ''} {getattr(user, 'last_name', '') or ''}".strip()
    return name or getattr(user, "email", None) or str(getattr(user, "id", ""))


def log_invoice_event(
    invoice,
    event_type: str,
    *,
    actor=None,
    actor_label: str = "",
    channel: str = InvoiceAuditEvent.CHANNEL_SYSTEM,
    summary: str = "",
    metadata: dict[str, Any] | None = None,
) -> InvoiceAuditEvent | None:
    """Record an immutable audit event. Never raises to callers."""
    if not invoice:
        return None
    try:
        return InvoiceAuditEvent.objects.create(
            invoice=invoice,
            restaurant_id=getattr(invoice, "restaurant_id", None),
            event_type=event_type,
            actor=actor if getattr(actor, "pk", None) else None,
            actor_label=actor_label or _actor_label(actor),
            channel=(channel or InvoiceAuditEvent.CHANNEL_SYSTEM)[:20],
            summary=(summary or "")[:4000],
            metadata=dict(metadata or {}),
        )
    except Exception:
        logger.exception("log_invoice_event failed invoice=%s type=%s", getattr(invoice, "id", None), event_type)
        return None
