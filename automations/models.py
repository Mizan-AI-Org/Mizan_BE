"""Per-tenant WhatsApp / ops automation workflows."""

from __future__ import annotations

import uuid

from django.db import models

from accounts.models import CustomUser, Restaurant


class TenantAutomation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(
        Restaurant, on_delete=models.CASCADE, related_name="automations"
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=False)
    trigger_type = models.CharField(max_length=64)
    trigger_config = models.JSONField(default=dict, blank=True)
    steps = models.JSONField(default=list, blank=True)
    template_id = models.CharField(max_length=64, blank=True, default="")
    run_count = models.PositiveIntegerField(default=0)
    last_run_at = models.DateTimeField(null=True, blank=True)
    stop_miya_on_match = models.BooleanField(
        default=False,
        help_text="When True, skip Miya for this message after automation runs.",
    )
    created_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_automations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tenant_automations"
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["restaurant", "is_active"]),
            models.Index(fields=["restaurant", "trigger_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.restaurant_id})"


class AutomationRunLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    automation = models.ForeignKey(
        TenantAutomation, on_delete=models.CASCADE, related_name="runs"
    )
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE)
    phone = models.CharField(max_length=32, blank=True, default="")
    trigger_event = models.CharField(max_length=64, blank=True, default="")
    success = models.BooleanField(default=True)
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "automation_run_logs"
        ordering = ["-created_at"]
