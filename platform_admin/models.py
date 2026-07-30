"""Platform-wide configuration stored in the database (ops-managed)."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class PlatformWhatsAppConfig(models.Model):
    """Singleton Meta WhatsApp Cloud API credentials for the central Miya number."""

    SINGLETON_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")

    id = models.UUIDField(primary_key=True, default=SINGLETON_ID, editable=False)
    phone_number_id = models.CharField(max_length=64, blank=True, default="")
    business_account_id = models.CharField(max_length=64, blank=True, default="")
    access_token_encrypted = models.TextField(blank=True, default="")
    verify_token = models.CharField(max_length=255, blank=True, default="")
    activation_phone = models.CharField(max_length=32, blank=True, default="212784476751")
    api_version = models.CharField(max_length=16, blank=True, default="v22.0")
    miya_whatsapp_enabled = models.BooleanField(default=True)
    miya_voice_default = models.BooleanField(default=False)
    display_phone_number = models.CharField(max_length=32, blank=True, default="")
    verified_name = models.CharField(max_length=255, blank=True, default="")
    last_probe_at = models.DateTimeField(null=True, blank=True)
    last_probe_ok = models.BooleanField(null=True, blank=True)
    last_probe_message = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="whatsapp_config_updates",
    )

    class Meta:
        verbose_name = "Platform WhatsApp config"
        verbose_name_plural = "Platform WhatsApp config"

    def __str__(self) -> str:
        return "Platform WhatsApp config"


class WhatsAppMessageTemplate(models.Model):
    """Cached Meta message template (synced from WhatsApp Manager)."""

    meta_id = models.CharField(max_length=64, unique=True, db_index=True)
    name = models.CharField(max_length=128, db_index=True)
    language = models.CharField(max_length=16, default="en_US")
    category = models.CharField(max_length=32, blank=True, default="")
    status = models.CharField(max_length=32, blank=True, default="")
    body_text = models.TextField(blank=True, default="")
    footer_text = models.TextField(blank=True, default="")
    header_text = models.TextField(blank=True, default="")
    components_json = models.JSONField(default=list, blank=True)
    synced_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "language"]
        verbose_name = "WhatsApp message template"
        verbose_name_plural = "WhatsApp message templates"

    def __str__(self) -> str:
        return f"{self.name} ({self.language})"
