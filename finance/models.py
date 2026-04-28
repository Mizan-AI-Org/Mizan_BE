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
