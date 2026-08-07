"""Miya tenant knowledge — uploaded documents managers and staff share with Miya."""

from __future__ import annotations

import uuid

from django.db import models

from accounts.models import CustomUser, Restaurant
from core.storage_paths import tenant_document_upload_path


class TenantDocument(models.Model):
    """Durable tenant file Miya can recall (widget + WhatsApp uploads → S3)."""

    SOURCE_WIDGET = "WIDGET"
    SOURCE_WHATSAPP = "WHATSAPP"
    SOURCE_CHOICES = (
        (SOURCE_WIDGET, "Dashboard widget"),
        (SOURCE_WHATSAPP, "WhatsApp"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(
        Restaurant, on_delete=models.CASCADE, related_name="tenant_documents"
    )
    location = models.ForeignKey(
        "accounts.BusinessLocation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tenant_documents",
        help_text="Establishment this upload belongs to (multi-site scoping).",
    )
    uploaded_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tenant_documents_uploaded",
    )
    uploader_phone = models.CharField(max_length=32, blank=True, default="")
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default=SOURCE_WIDGET)
    title = models.CharField(max_length=255)
    original_filename = models.CharField(max_length=255, blank=True, default="")
    mime_type = models.CharField(max_length=128, blank=True, default="")
    file = models.FileField(upload_to=tenant_document_upload_path, blank=True, default="")
    storage_path = models.CharField(max_length=512, blank=True, default="")
    file_url = models.URLField(max_length=1000, blank=True, default="")
    category = models.CharField(max_length=64, blank=True, default="other")
    summary = models.TextField(blank=True, default="")
    extracted_text = models.TextField(blank=True, default="")
    parse_metadata = models.JSONField(default=dict, blank=True)
    # Denormalized OCR intelligence — Miya queries these, not raw text alone.
    structured_fields = models.JSONField(default=dict, blank=True)
    vendor_name = models.CharField(max_length=255, blank=True, default="")
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=8, blank=True, default="")
    invoice_number = models.CharField(max_length=128, blank=True, default="")
    expiry_date = models.DateField(null=True, blank=True)
    compliance_document = models.ForeignKey(
        "payroll.ComplianceDocument",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tenant_files",
    )
    invoice = models.ForeignKey(
        "finance.Invoice",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tenant_documents",
    )
    tags = models.JSONField(default=list, blank=True)
    # Phase 14.3.1 — document versioning (immutable history; new row per version)
    content_hash = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="SHA-256 hex digest of stored file bytes.",
    )
    supersedes = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="superseded_by_versions",
        help_text="Prior version this upload replaces (same logical document family).",
    )
    document_family_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Groups all versions of the same logical document.",
    )
    is_current = models.BooleanField(
        default=True,
        db_index=True,
        help_text="True for the latest version within a document family.",
    )
    version_number = models.PositiveIntegerField(default=1)
    processing_status = models.CharField(
        max_length=16,
        choices=(
            ("pending", "Pending"),
            ("ok", "OK"),
            ("failed", "Failed"),
            ("skipped", "Skipped"),
        ),
        default="pending",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["restaurant", "-created_at"]),
            models.Index(fields=["restaurant", "category"]),
            models.Index(fields=["restaurant", "content_hash"]),
            models.Index(fields=["restaurant", "document_family_id", "is_current"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.restaurant_id})"


class OperationalEvent(models.Model):
    """
    Durable operational event log — survives process restart.
    Authoritative observations for Miya Operational Memory / Event History.
    Conversation memory must never override these records.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(
        Restaurant, on_delete=models.CASCADE, related_name="operational_events"
    )
    location = models.ForeignKey(
        "accounts.BusinessLocation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="operational_events",
    )
    actor = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="operational_events_acted",
    )
    event_type = models.CharField(max_length=64, db_index=True)
    entity_type = models.CharField(max_length=32, blank=True, default="", db_index=True)
    entity_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    entity_label = models.CharField(max_length=255, blank=True, default="")
    summary = models.CharField(max_length=512, blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    channel = models.CharField(max_length=32, blank=True, default="")
    operation_id = models.CharField(max_length=128, blank=True, default="", db_index=True)
    message_id = models.CharField(max_length=128, blank=True, default="")
    conversation_id = models.CharField(max_length=128, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["restaurant", "-created_at"]),
            models.Index(fields=["restaurant", "entity_type", "entity_id"]),
            models.Index(fields=["restaurant", "event_type", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} {self.entity_type}:{self.entity_id}"


class WorkingMemorySnapshot(models.Model):
    """
    Durable working-memory pointers per user × organization.
    Holds IDs/labels of current focus — NOT entity status (status always from DB).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(
        Restaurant, on_delete=models.CASCADE, related_name="working_memory_snapshots"
    )
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="working_memory_snapshots",
    )
    establishment_id = models.CharField(max_length=64, blank=True, default="")
    establishment_name = models.CharField(max_length=255, blank=True, default="")
    department = models.CharField(max_length=128, blank=True, default="")
    current_task_id = models.CharField(max_length=64, blank=True, default="")
    current_task_label = models.CharField(max_length=255, blank=True, default="")
    current_incident_id = models.CharField(max_length=64, blank=True, default="")
    current_incident_label = models.CharField(max_length=255, blank=True, default="")
    current_document_id = models.CharField(max_length=64, blank=True, default="")
    current_document_label = models.CharField(max_length=255, blank=True, default="")
    current_invoice_id = models.CharField(max_length=64, blank=True, default="")
    current_invoice_label = models.CharField(max_length=255, blank=True, default="")
    current_workflow = models.CharField(max_length=128, blank=True, default="")
    extra = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["restaurant", "user"],
                name="miya_working_memory_unique_user_org",
            )
        ]
        indexes = [
            models.Index(fields=["restaurant", "user"]),
        ]

    def __str__(self) -> str:
        return f"working_memory u={self.user_id} org={self.restaurant_id}"

    def as_dict(self) -> dict:
        return {
            "establishment_id": self.establishment_id or None,
            "establishment_name": self.establishment_name or None,
            "department": self.department or None,
            "current_task_id": self.current_task_id or None,
            "current_task_label": self.current_task_label or None,
            "current_incident_id": self.current_incident_id or None,
            "current_incident_label": self.current_incident_label or None,
            "current_document_id": self.current_document_id or None,
            "current_document_label": self.current_document_label or None,
            "current_invoice_id": self.current_invoice_id or None,
            "current_invoice_label": self.current_invoice_label or None,
            "current_workflow": self.current_workflow or None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "authority": "working_memory_pointers_only",
            "directive": (
                "These are focus pointers only. Always re-fetch status via get_current_* / DB."
            ),
        }
