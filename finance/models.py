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
- Status transitions are intentionally simple: DRAFT → OPEN → PAID
  (or VOIDED). OVERDUE is computed on the fly from ``due_date < today``
  and ``status == 'OPEN'`` so the widget can highlight late bills
  without needing a beat task to flip them.
- ``photo`` lets WhatsApp users send a phone snap of the invoice and
  Miya store the URL on the row — useful audit trail.
- Indexed for the two read paths the widget cares about:
    1. "open invoices for tenant ordered by due_date"
    2. "did we already record vendor X invoice Y" (dedupe)
"""
from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone

from accounts.models import BusinessLocation, CustomUser, Restaurant


class Invoice(models.Model):
    """An accounts-payable invoice owed by the tenant."""

    STATUS_DRAFT = "DRAFT"
    STATUS_OPEN = "OPEN"
    STATUS_PAID = "PAID"
    STATUS_VOIDED = "VOIDED"

    STATUS_CHOICES = (
        (STATUS_DRAFT, "Draft"),
        (STATUS_OPEN, "Open"),
        (STATUS_PAID, "Paid"),
        (STATUS_VOIDED, "Voided"),
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
        max_length=12,
        choices=STATUS_CHOICES,
        default=STATUS_OPEN,
    )

    # Free-text category so managers can tag bills "rent", "electricity",
    # "deepclean", etc. We deliberately don't constrain this — discovery
    # of the actual buckets matters more than locking it down.
    category = models.CharField(max_length=50, blank=True, default="")
    notes = models.TextField(blank=True, default="")

    photo = models.ImageField(
        upload_to="invoices/",
        null=True,
        blank=True,
        help_text="Snapshot of the printed/PDF invoice.",
    )
    attachment = models.FileField(
        upload_to="invoices/",
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
    def is_overdue(self) -> bool:
        """Computed at read time so we don't need a beat task to flip
        the status (and so VOIDED/PAID rows never look overdue)."""
        if self.status != self.STATUS_OPEN or not self.due_date:
            return False
        return self.due_date < timezone.now().date()

    @property
    def days_until_due(self) -> int | None:
        """Negative when overdue. None when we have no due date."""
        if not self.due_date:
            return None
        return (self.due_date - timezone.now().date()).days

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
