from __future__ import annotations

import uuid
from datetime import timedelta

from django.db import models
from django.utils import timezone

from accounts.models import CustomUser, Restaurant


class Payslip(models.Model):
    STATUS_DRAFT = "DRAFT"
    STATUS_ISSUED = "ISSUED"
    STATUS_CHOICES = (
        (STATUS_DRAFT, "Draft"),
        (STATUS_ISSUED, "Issued"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="payslips")
    staff = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="payslips")
    period_start = models.DateField()
    period_end = models.DateField()
    hours_worked = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    hourly_rate = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    gross_pay = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=8, default="MAD")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_ISSUED)
    notes = models.TextField(blank=True, default="")
    pdf_url = models.URLField(max_length=1024, blank=True, default="")
    created_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payslips_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-period_end", "staff__last_name"]
        indexes = [
            models.Index(fields=["restaurant", "period_start", "period_end"]),
            models.Index(fields=["restaurant", "staff", "period_end"]),
        ]
        unique_together = [("restaurant", "staff", "period_start", "period_end")]

    def __str__(self) -> str:
        return f"Payslip {self.staff_id} {self.period_start}–{self.period_end}"


class ComplianceReminder(models.Model):
    CATEGORY_CNSS = "CNSS"
    CATEGORY_TAX = "TAX"
    CATEGORY_LABOR = "LABOR"
    CATEGORY_OTHER = "OTHER"
    CATEGORY_CHOICES = (
        (CATEGORY_CNSS, "CNSS"),
        (CATEGORY_TAX, "Tax"),
        (CATEGORY_LABOR, "Labor"),
        (CATEGORY_OTHER, "Other"),
    )

    STATUS_UPCOMING = "UPCOMING"
    STATUS_NOTIFIED = "NOTIFIED"
    STATUS_DONE = "DONE"
    STATUS_CHOICES = (
        (STATUS_UPCOMING, "Upcoming"),
        (STATUS_NOTIFIED, "Notified"),
        (STATUS_DONE, "Done"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="compliance_reminders")
    code = models.CharField(max_length=64)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    category = models.CharField(max_length=16, choices=CATEGORY_CHOICES, default=CATEGORY_OTHER)
    due_date = models.DateField()
    remind_days_before = models.PositiveSmallIntegerField(default=7)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_UPCOMING)
    last_notified_at = models.DateTimeField(null=True, blank=True)
    external_id = models.CharField(max_length=128, blank=True, default="", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["due_date", "title"]
        indexes = [
            models.Index(fields=["restaurant", "due_date", "status"]),
        ]

    @property
    def is_due_soon(self) -> bool:
        if self.status == self.STATUS_DONE:
            return False
        horizon = timezone.now().date() + timedelta(days=self.remind_days_before)
        return self.due_date <= horizon


class ComplianceDocument(models.Model):
    """
    Restaurant-owned certificates / permits with expiry dates.
    Miya reminds owners & managers before they lapse (insurance, hygiene, etc.).
    """

    STATUS_ACTIVE = "ACTIVE"
    STATUS_EXPIRED = "EXPIRED"
    STATUS_ARCHIVED = "ARCHIVED"
    STATUS_CHOICES = (
        (STATUS_ACTIVE, "Active"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_ARCHIVED, "Archived"),
    )

    TYPE_BUSINESS_REGISTRATION = "BUSINESS_REGISTRATION"
    TYPE_INSURANCE = "INSURANCE"
    TYPE_FIRE_EXTINGUISHER = "FIRE_EXTINGUISHER"
    TYPE_HYGIENE = "HYGIENE"
    TYPE_HEALTH_PERMIT = "HEALTH_PERMIT"
    TYPE_LIQUOR_LICENSE = "LIQUOR_LICENSE"
    TYPE_EQUIPMENT_INSPECTION = "EQUIPMENT_INSPECTION"
    TYPE_OTHER = "OTHER"
    TYPE_CHOICES = (
        (TYPE_BUSINESS_REGISTRATION, "Business registration"),
        (TYPE_INSURANCE, "Insurance"),
        (TYPE_FIRE_EXTINGUISHER, "Fire extinguisher inspection"),
        (TYPE_HYGIENE, "Hygiene / food safety certificate"),
        (TYPE_HEALTH_PERMIT, "Health permit"),
        (TYPE_LIQUOR_LICENSE, "Liquor license"),
        (TYPE_EQUIPMENT_INSPECTION, "Equipment inspection"),
        (TYPE_OTHER, "Other"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(
        Restaurant, on_delete=models.CASCADE, related_name="compliance_documents"
    )
    document_type = models.CharField(max_length=40, choices=TYPE_CHOICES, default=TYPE_OTHER)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    reference_number = models.CharField(max_length=128, blank=True, default="")
    expires_at = models.DateField(null=True, blank=True)
    remind_days_before = models.PositiveSmallIntegerField(
        default=30,
        help_text="Start reminding this many days before expiry",
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    last_notified_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="compliance_documents_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["expires_at", "title"]
        indexes = [
            models.Index(fields=["restaurant", "status", "expires_at"]),
            models.Index(fields=["restaurant", "document_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.document_type})"

    @property
    def is_in_reminder_window(self) -> bool:
        if self.status == self.STATUS_ARCHIVED or not self.expires_at:
            return False
        today = timezone.now().date()
        if self.expires_at < today:
            return True  # already expired — keep reminding until renewed
        horizon = today + timedelta(days=self.remind_days_before or 30)
        return self.expires_at <= horizon


class TemperatureReading(models.Model):

    SOURCE_WHATSAPP = "WHATSAPP"
    SOURCE_CHECKLIST = "CHECKLIST"
    SOURCE_MANUAL = "MANUAL"
    SOURCE_CHOICES = (
        (SOURCE_WHATSAPP, "WhatsApp"),
        (SOURCE_CHECKLIST, "Checklist"),
        (SOURCE_MANUAL, "Manual"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="temperature_readings")
    equipment = models.CharField(max_length=120)
    value_c = models.DecimalField(max_digits=5, decimal_places=2)
    recorded_at = models.DateTimeField(default=timezone.now)
    recorded_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="temperature_readings",
    )
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=SOURCE_WHATSAPP)
    notes = models.TextField(blank=True, default="")
    is_out_of_range = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-recorded_at"]
        indexes = [
            models.Index(fields=["restaurant", "recorded_at"]),
            models.Index(fields=["restaurant", "equipment", "recorded_at"]),
        ]


class DeliveryMenuSnapshot(models.Model):
    """Cached menu export for delivery-aggregator sync (Glovo-first)."""

    PROVIDER_GLOVO = "GLOVO"
    PROVIDER_CHOICES = ((PROVIDER_GLOVO, "Glovo"),)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="delivery_menu_snapshots")
    provider = models.CharField(max_length=32, choices=PROVIDER_CHOICES, default=PROVIDER_GLOVO)
    item_count = models.PositiveIntegerField(default=0)
    payload = models.JSONField(default=dict, blank=True)
    synced_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-synced_at"]
