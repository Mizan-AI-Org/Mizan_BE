"""
Finance / Accounts Payable models.

Tracks bills the restaurant owes (suppliers, utilities, rent, taxes,
maintenance contractors). Powers the Finance widget on the dashboard
and gives Miya structured invoice tools instead of free-text staff
requests with category=FINANCE.

Design notes:
- One ``Invoice`` row per bill the manager wants to track. We do NOT
  try to be a full accounting system — no GL, no journal entries, no
  multi-line invoices for now. The unit of work is "this bill, due on
  this date, paid or not paid".
- Status lifecycle (richer than the original DRAFT/OPEN/PAID/VOIDED):
  DRAFT → SUBMITTED → UNDER_REVIEW → PENDING_APPROVAL → APPROVED →
  PAYMENT_IN_PROGRESS → PAID (or REJECTED / RETURNED / PAYMENT_FAILED /
  VOIDED). Legacy ``OPEN`` remains valid and is treated as an unpaid
  active bill. PayGuard still uses ``approval_status`` in parallel;
  ``lifecycle_status`` merges both for agent/UI display.
- ``proof_of_payment`` stores the receipt / transfer confirmation.
- ``returned_reason`` explains RETURNED corrections.
- OVERDUE is computed on the fly from ``due_date < today`` and an
  unpaid-active status so the widget can highlight late bills without
  needing a beat task to flip them.
"""
from __future__ import annotations

import uuid

from django.db import models

from core.storage_paths import (
    invoice_photo_upload_path,
    invoice_upload_path,
    payment_proof_upload_path,
)
from django.utils import timezone

from accounts.models import BusinessLocation, CustomUser, Restaurant


class Invoice(models.Model):
    """An accounts-payable invoice owed by the tenant."""

    STATUS_DRAFT = "DRAFT"
    STATUS_SUBMITTED = "SUBMITTED"
    STATUS_UNDER_REVIEW = "UNDER_REVIEW"
    STATUS_PENDING_APPROVAL = "PENDING_APPROVAL"
    STATUS_APPROVED = "APPROVED"
    STATUS_REJECTED = "REJECTED"
    STATUS_RETURNED = "RETURNED"
    STATUS_OPEN = "OPEN"  # legacy open-for-payment (treated like SUBMITTED/APPROVED)
    STATUS_PAYMENT_IN_PROGRESS = "PAYMENT_IN_PROGRESS"
    STATUS_PAID = "PAID"
    STATUS_PAYMENT_FAILED = "PAYMENT_FAILED"
    STATUS_VOIDED = "VOIDED"

    STATUS_CHOICES = (
        (STATUS_DRAFT, "Draft"),
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_UNDER_REVIEW, "Under review"),
        (STATUS_PENDING_APPROVAL, "Pending approval"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_RETURNED, "Returned"),
        (STATUS_OPEN, "Open"),
        (STATUS_PAYMENT_IN_PROGRESS, "Payment in progress"),
        (STATUS_PAID, "Paid"),
        (STATUS_PAYMENT_FAILED, "Payment failed"),
        (STATUS_VOIDED, "Voided"),
    )

    # Statuses that still represent an unpaid bill due on ``due_date``.
    UNPAID_ACTIVE_STATUSES = frozenset(
        {
            STATUS_OPEN,
            STATUS_SUBMITTED,
            STATUS_UNDER_REVIEW,
            STATUS_PENDING_APPROVAL,
            STATUS_APPROVED,
            STATUS_PAYMENT_IN_PROGRESS,
            STATUS_PAYMENT_FAILED,
            STATUS_RETURNED,
        }
    )

    PAYMENT_METHOD_CHOICES = (
        ("CASH", "Cash"),
        ("CARD", "Card"),
        ("BANK_TRANSFER", "Bank transfer"),
        ("CHEQUE", "Cheque"),
        ("DIRECT_DEBIT", "Direct debit"),
        ("OTHER", "Other"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(
        Restaurant, on_delete=models.CASCADE, related_name="invoices"
    )
    location = models.ForeignKey(
        BusinessLocation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
        help_text="Which branch this bill belongs to (optional, defaults to tenant primary).",
    )

    # Vendor — kept as plain text rather than a Vendor FK because most
    # restaurants pay a long tail of one-off suppliers. Future: if a
    # tenant wants supplier rollups we promote this into a relation.
    vendor_name = models.CharField(max_length=200)
    invoice_number = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="Invoice number printed on the bill, used for dedupe.",
    )

    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=8, default="USD")

    issue_date = models.DateField(null=True, blank=True)
    due_date = models.DateField()

    status = models.CharField(
        max_length=24,
        choices=STATUS_CHOICES,
        default=STATUS_OPEN,
    )

    # Free-text category so managers can tag bills "rent", "electricity",
    # "deepclean", etc. We deliberately don't constrain this — discovery
    # of the actual buckets matters more than locking it down.
    category = models.CharField(max_length=50, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    returned_reason = models.TextField(
        blank=True,
        default="",
        help_text="Why the invoice was returned for correction.",
    )

    photo = models.ImageField(
        upload_to=invoice_photo_upload_path,
        null=True,
        blank=True,
        help_text="Snapshot of the printed/PDF invoice.",
    )
    attachment = models.FileField(
        upload_to=invoice_upload_path,
        null=True,
        blank=True,
        help_text="Original invoice scan (image or PDF) from WhatsApp / upload.",
    )
    attachment_content_type = models.CharField(max_length=100, blank=True, default="")
    attachment_filename = models.CharField(max_length=255, blank=True, default="")
    photo_url = models.URLField(
        max_length=1024,
        blank=True,
        default="",
        help_text="External URL when the photo is hosted off-platform (e.g. WhatsApp media).",
    )
    proof_of_payment = models.FileField(
        upload_to=payment_proof_upload_path,
        null=True,
        blank=True,
        help_text="Receipt / transfer confirmation attached after payment.",
    )

    paid_at = models.DateTimeField(null=True, blank=True)
    paid_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    payment_method = models.CharField(
        max_length=20, blank=True, default="", choices=PAYMENT_METHOD_CHOICES
    )
    payment_reference = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="Cheque number, transfer reference, or POS receipt id.",
    )

    BANK_PAYMENT_PENDING = "PENDING"
    BANK_PAYMENT_INITIATED = "INITIATED"
    BANK_PAYMENT_CLEARED = "CLEARED"
    BANK_PAYMENT_FAILED = "FAILED"
    BANK_PAYMENT_NA = "NOT_APPLICABLE"
    BANK_PAYMENT_STATUS_CHOICES = (
        (BANK_PAYMENT_NA, "Not applicable"),
        (BANK_PAYMENT_PENDING, "Pending"),
        (BANK_PAYMENT_INITIATED, "Initiated"),
        (BANK_PAYMENT_CLEARED, "Cleared"),
        (BANK_PAYMENT_FAILED, "Failed"),
    )
    bank_payment_status = models.CharField(
        max_length=20,
        choices=BANK_PAYMENT_STATUS_CHOICES,
        default=BANK_PAYMENT_PENDING,
        help_text="Tracks bank transfer / cheque payment lifecycle before/after mark paid.",
    )
    bank_payment_note = models.CharField(max_length=255, blank=True, default="")

    # Light PO ↔ invoice reconciliation (manager copilot / finance agent)
    MATCH_UNMATCHED = "UNMATCHED"
    MATCH_SUGGESTED = "SUGGESTED"
    MATCH_CONFIRMED = "CONFIRMED"
    MATCH_STATUS_CHOICES = (
        (MATCH_UNMATCHED, "Unmatched"),
        (MATCH_SUGGESTED, "Suggested"),
        (MATCH_CONFIRMED, "Confirmed"),
    )
    purchase_order = models.ForeignKey(
        "inventory.PurchaseOrder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
        help_text="Linked purchase order when AP invoice is reconciled to a PO.",
    )
    match_status = models.CharField(
        max_length=12,
        choices=MATCH_STATUS_CHOICES,
        default=MATCH_UNMATCHED,
        db_index=True,
    )
    match_confidence = models.DecimalField(
        max_digits=4,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="0–1 score from the matcher when status is SUGGESTED/CONFIRMED.",
    )

    # PayGuard — amount-tiered payment approval (see payment_approval.py)
    APPROVAL_NONE = "NONE"
    APPROVAL_PENDING = "PENDING_APPROVAL"
    APPROVAL_APPROVED = "APPROVED"
    APPROVAL_REJECTED = "REJECTED"
    APPROVAL_STATUS_CHOICES = (
        (APPROVAL_NONE, "Not required"),
        (APPROVAL_PENDING, "Pending approval"),
        (APPROVAL_APPROVED, "Approved to pay"),
        (APPROVAL_REJECTED, "Rejected"),
    )
    approval_status = models.CharField(
        max_length=20,
        choices=APPROVAL_STATUS_CHOICES,
        default=APPROVAL_NONE,
        db_index=True,
        help_text="PayGuard ladder status before mark-paid is allowed.",
    )

    created_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices_created",
    )
    paid_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices_paid",
    )
    # Payment / follow-up owner (Miya assign_invoice, Finance board "To").
    assigned_to = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices_assigned",
        help_text="Staff responsible for paying / chasing this invoice.",
    )
    assigned_at = models.DateTimeField(null=True, blank=True)
    assigned_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices_assigned_by",
    )
    # Vision / OCR extraction confidence from photo or document router (0–1).
    ocr_confidence = models.DecimalField(
        max_digits=4,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="0–1 classification/extraction confidence when created from a photo/scan.",
    )
    ocr_fields = models.JSONField(
        default=dict,
        blank=True,
        help_text="Raw extracted fields + per-field notes from OCR/vision.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["due_date", "-created_at"]
        indexes = [
            # Powers the Finance widget: "all OPEN invoices for tenant
            # ordered by upcoming due date."
            models.Index(fields=["restaurant", "status", "due_date"]),
            # Powers dedupe: "did we already record vendor X invoice Y?"
            models.Index(fields=["restaurant", "vendor_name", "invoice_number"]),
        ]

    def __str__(self) -> str:
        return f"{self.vendor_name} {self.invoice_number or ''} — {self.amount} {self.currency}".strip()

    @property
    def lifecycle_status(self) -> str:
        """
        Unified lifecycle view combining ``status`` and PayGuard ``approval_status``.

        Prefer the row's explicit ``status`` when it is already a rich lifecycle
        value; otherwise map legacy OPEN + approval_status into the richer set.
        """
        if self.status in {
            self.STATUS_DRAFT,
            self.STATUS_SUBMITTED,
            self.STATUS_UNDER_REVIEW,
            self.STATUS_APPROVED,
            self.STATUS_REJECTED,
            self.STATUS_RETURNED,
            self.STATUS_PAYMENT_IN_PROGRESS,
            self.STATUS_PAID,
            self.STATUS_PAYMENT_FAILED,
            self.STATUS_VOIDED,
            self.STATUS_PENDING_APPROVAL,
        }:
            return self.status
        # Legacy OPEN (+ approval overlay)
        if self.approval_status == self.APPROVAL_PENDING:
            return self.STATUS_PENDING_APPROVAL
        if self.approval_status == self.APPROVAL_REJECTED:
            return self.STATUS_REJECTED
        if self.approval_status == self.APPROVAL_APPROVED:
            return self.STATUS_APPROVED
        return self.STATUS_OPEN

    @property
    def is_overdue(self) -> bool:
        """Computed at read time so we don't need a beat task to flip
        the status (and so VOIDED/PAID rows never look overdue)."""
        if self.status not in self.UNPAID_ACTIVE_STATUSES or not self.due_date:
            return False
        return self.due_date < timezone.now().date()

    @property
    def days_until_due(self) -> int | None:
        """Negative when overdue. None when we have no due date."""
        if not self.due_date:
            return None
        return (self.due_date - timezone.now().date()).days

    def mark_payment_in_progress(self) -> None:
        """Flip to PAYMENT_IN_PROGRESS when a transfer/cheque is initiated."""
        if self.status == self.STATUS_PAID:
            return
        self.status = self.STATUS_PAYMENT_IN_PROGRESS
        self.save(update_fields=["status", "updated_at"])

    def mark_payment_failed(self, *, note: str = "") -> None:
        self.status = self.STATUS_PAYMENT_FAILED
        update_fields = ["status", "updated_at"]
        if note:
            self.bank_payment_note = note[:255]
            update_fields.append("bank_payment_note")
        self.save(update_fields=update_fields)

    def mark_paid(
        self,
        *,
        paid_on=None,
        method: str = "",
        reference: str = "",
        amount=None,
        user: CustomUser | None = None,
    ) -> None:
        """
        Idempotent transition to PAID. ``paid_on`` accepts a date or
        datetime; date gets bumped to ``timezone.now()`` so we keep a
        precise audit timestamp.
        """
        from datetime import date, datetime as _dt

        if isinstance(paid_on, _dt):
            self.paid_at = paid_on
        elif isinstance(paid_on, date):
            self.paid_at = timezone.make_aware(_dt.combine(paid_on, _dt.min.time())) \
                if timezone.is_naive(_dt.combine(paid_on, _dt.min.time())) \
                else _dt.combine(paid_on, _dt.min.time())
        else:
            self.paid_at = timezone.now()

        self.status = self.STATUS_PAID
        if method:
            self.payment_method = method[:20]
        if reference:
            self.payment_reference = reference[:120]
        if amount is not None:
            self.paid_amount = amount
        elif self.paid_amount is None:
            self.paid_amount = self.amount
        if user is not None:
            self.paid_by = user
        self.save(
            update_fields=[
                "status",
                "paid_at",
                "paid_amount",
                "payment_method",
                "payment_reference",
                "paid_by",
                "updated_at",
            ]
        )


class InvoicePaymentApproval(models.Model):
    """One PayGuard run for an invoice — walks ordered steps until paid-ready."""

    STATUS_PENDING = "PENDING"
    STATUS_APPROVED = "APPROVED"
    STATUS_REJECTED = "REJECTED"
    STATUS_CANCELLED = "CANCELLED"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_CANCELLED, "Cancelled"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.OneToOneField(
        Invoice, on_delete=models.CASCADE, related_name="payment_approval"
    )
    restaurant = models.ForeignKey(
        Restaurant, on_delete=models.CASCADE, related_name="payment_approvals"
    )
    tier_id = models.CharField(max_length=64, blank=True, default="")
    tier_name = models.CharField(max_length=120, blank=True, default="")
    current_step_index = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    requested_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_approvals_requested",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_reminded_at = models.DateTimeField(null=True, blank=True)
    reminder_count = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["restaurant", "status", "started_at"]),
        ]

    def __str__(self) -> str:
        return f"PayGuard {self.invoice_id} step {self.current_step_index} ({self.status})"


class InvoicePaymentApprovalStep(models.Model):
    """A single rung on the PayGuard ladder for one invoice."""

    STATUS_WAITING = "WAITING"
    STATUS_NOTIFIED = "NOTIFIED"
    STATUS_APPROVED = "APPROVED"
    STATUS_REJECTED = "REJECTED"
    STATUS_SKIPPED = "SKIPPED"
    STATUS_CHOICES = (
        (STATUS_WAITING, "Waiting"),
        (STATUS_NOTIFIED, "Notified"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_SKIPPED, "Skipped"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    approval = models.ForeignKey(
        InvoicePaymentApproval, on_delete=models.CASCADE, related_name="steps"
    )
    step_order = models.PositiveSmallIntegerField()
    label = models.CharField(max_length=120, blank=True, default="")
    required_role = models.CharField(max_length=32, blank=True, default="")
    required_user = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_approval_steps",
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_WAITING)
    acted_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_approvals_acted",
    )
    acted_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True, default="")
    notified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["step_order"]
        unique_together = [("approval", "step_order")]

    def __str__(self) -> str:
        return f"Step {self.step_order} {self.label or self.required_role} ({self.status})"


# Immutable invoice audit trail (defined in audit.py, re-exported for Django).
from finance.audit import InvoiceAuditEvent  # noqa: E402, F401
