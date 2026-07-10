"""
WhatsApp-first personal & team memory layer (Memorae-parity).

Captures notes, reminders, and lists that Miya can save and recall weeks later
with context (who / why / entities / project), without replacing ops AgentMemory.
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class MemoryNote(models.Model):
    """
    Contextual knowledge item — the Memorae "save this / recall later" unit.

    Example: "Save this content idea for Brand X's Ramadan campaign"
    → content, project_key=ramadan-brand-x, entities=["Brand X","Ramadan"], why=...
    """

    VISIBILITY = (
        ("personal", "Personal"),
        ("team", "Team (restaurant)"),
        ("department", "Department"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(
        "accounts.Restaurant",
        on_delete=models.CASCADE,
        related_name="memory_notes",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="memory_notes_owned",
        help_text="Who saved this (personal scope filter).",
    )
    visibility = models.CharField(max_length=20, choices=VISIBILITY, default="personal", db_index=True)
    department = models.CharField(max_length=64, blank=True, default="")

    content = models.TextField(help_text="What was said / saved")
    why = models.TextField(blank=True, default="", help_text="Why it mattered / decision rationale")
    people = models.JSONField(
        default=list,
        blank=True,
        help_text='People involved, e.g. ["Karim", "supplier Sysco"]',
    )
    entities = models.JSONField(
        default=list,
        blank=True,
        help_text='Named entities: brands, products, campaigns, sites',
    )
    project_key = models.CharField(
        max_length=120,
        blank=True,
        default="",
        db_index=True,
        help_text='Thread/project slug, e.g. "ramadan-2026", "terrace-redesign"',
    )
    tags = models.JSONField(default=list, blank=True)

    # Optional links into ops records
    linked_task_id = models.UUIDField(null=True, blank=True)
    linked_staff_request_id = models.UUIDField(null=True, blank=True)
    linked_invoice_id = models.UUIDField(null=True, blank=True)
    media_url = models.URLField(max_length=1000, blank=True, default="")
    media_type = models.CharField(max_length=40, blank=True, default="")

    source_channel = models.CharField(max_length=20, default="whatsapp")
    source_phone = models.CharField(max_length=40, blank=True, default="")

    # For serendipity / resurfacing
    last_recalled_at = models.DateTimeField(null=True, blank=True)
    recall_count = models.PositiveIntegerField(default=0)
    is_archived = models.BooleanField(default=False)

    search_text = models.TextField(
        blank=True,
        default="",
        help_text="Denormalized blob for icontains / full-text search",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "memory_notes"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["restaurant", "visibility", "-created_at"]),
            models.Index(fields=["restaurant", "project_key"]),
            models.Index(fields=["restaurant", "owner", "-created_at"]),
        ]

    def rebuild_search_text(self) -> None:
        parts = [
            self.content or "",
            self.why or "",
            self.project_key or "",
            self.department or "",
            " ".join(self.people or []),
            " ".join(self.entities or []),
            " ".join(self.tags or []),
        ]
        self.search_text = " ".join(p for p in parts if p).strip()

    def save(self, *args, **kwargs):
        self.rebuild_search_text()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"MemoryNote {self.id}: {(self.content or '')[:60]}"


class MemoryList(models.Model):
    """Named list managed via WhatsApp (shopping, prep, packing, TODOs)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(
        "accounts.Restaurant",
        on_delete=models.CASCADE,
        related_name="memory_lists",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="memory_lists_owned",
    )
    name = models.CharField(max_length=120)
    visibility = models.CharField(
        max_length=20,
        choices=MemoryNote.VISIBILITY,
        default="personal",
    )
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "memory_lists"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["restaurant", "owner", "name"],
                name="uniq_memory_list_per_owner_name",
            ),
        ]
        indexes = [
            models.Index(fields=["restaurant", "owner"]),
        ]

    def __str__(self):
        return f"List {self.name} ({self.restaurant_id})"


class MemoryListItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    memory_list = models.ForeignKey(
        MemoryList,
        on_delete=models.CASCADE,
        related_name="items",
    )
    text = models.CharField(max_length=500)
    is_checked = models.BooleanField(default=False)
    order_index = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "memory_list_items"
        ordering = ["order_index", "created_at"]

    def __str__(self):
        mark = "✓" if self.is_checked else "○"
        return f"{mark} {self.text[:40]}"


class PersonalReminder(models.Model):
    """
    WhatsApp-fireable one-shot or recurring reminder (Memorae-style).
    Distinct from Google Calendar create_reminder and dashboard Task personal ops.
    """

    STATUS = (
        ("pending", "Pending"),
        ("fired", "Fired"),
        ("cancelled", "Cancelled"),
        ("failed", "Failed"),
    )
    RECURRENCE = (
        ("none", "One-shot"),
        ("daily", "Daily"),
        ("weekly", "Weekly"),
        ("monthly", "Monthly"),
        ("weekdays", "Weekdays (Mon–Fri)"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(
        "accounts.Restaurant",
        on_delete=models.CASCADE,
        related_name="personal_reminders",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="personal_reminders",
    )
    phone = models.CharField(
        max_length=40,
        blank=True,
        default="",
        help_text="E.164-ish digits for WhatsApp delivery",
    )
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True, default="")
    due_at = models.DateTimeField(db_index=True)
    timezone_name = models.CharField(max_length=64, default="Africa/Casablanca")
    recurrence = models.CharField(max_length=20, choices=RECURRENCE, default="none")
    status = models.CharField(max_length=20, choices=STATUS, default="pending", db_index=True)
    fired_at = models.DateTimeField(null=True, blank=True)
    fire_count = models.PositiveIntegerField(default=0)
    linked_note = models.ForeignKey(
        MemoryNote,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reminders",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "personal_reminders"
        ordering = ["due_at"]
        indexes = [
            models.Index(fields=["status", "due_at"]),
            models.Index(fields=["restaurant", "owner", "status"]),
        ]

    def __str__(self):
        return f"Reminder {self.title} @ {self.due_at}"
