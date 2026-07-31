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
    compliance_document = models.ForeignKey(
        "payroll.ComplianceDocument",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tenant_files",
    )
    tags = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["restaurant", "-created_at"]),
            models.Index(fields=["restaurant", "category"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.restaurant_id})"
