"""
Manager-facing Invoice API.

Endpoints
---------
- ``GET  /api/finance/invoices/``                list (filterable by status, vendor, due window)
- ``POST /api/finance/invoices/``                create
- ``GET  /api/finance/invoices/<id>/``           retrieve
- ``PATCH /api/finance/invoices/<id>/``          update
- ``DELETE /api/finance/invoices/<id>/``         soft delete (sets status=VOIDED)
- ``POST /api/finance/invoices/<id>/mark-paid/`` transition to PAID

All routes are tenant-scoped to ``request.user.restaurant``.

Manager-only writes (OWNER/ADMIN/MANAGER/SUPER_ADMIN); read access is
the same set.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from .models import Invoice
from .serializers import InvoiceSerializer

logger = logging.getLogger(__name__)

_FINANCE_ROLES = {"SUPER_ADMIN", "ADMIN", "OWNER", "MANAGER"}


class InvoiceViewSet(ModelViewSet):
    """Tenant-scoped CRUD over ``Invoice``."""

    serializer_class = InvoiceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        restaurant = getattr(user, "restaurant", None)
        if restaurant is None:
            return Invoice.objects.none()
        qs = Invoice.objects.filter(restaurant=restaurant).select_related(
            "location", "created_by", "paid_by"
        )

        # Filtering
        params = self.request.query_params
        st = (params.get("status") or "").upper()
        valid_statuses = {c[0] for c in Invoice.STATUS_CHOICES}
        if st in valid_statuses:
            qs = qs.filter(status=st)

        vendor = (params.get("vendor") or "").strip()
        if vendor:
            qs = qs.filter(vendor_name__icontains=vendor)

        # ``overdue=true`` — unpaid active + due_date < today.
        overdue = (params.get("overdue") or "").lower() in ("true", "1", "yes")
        if overdue:
            qs = qs.filter(
                status__in=Invoice.UNPAID_ACTIVE_STATUSES,
                due_date__lt=timezone.now().date(),
            )

        # ``due_within=N`` days — unpaid active + due_date <= today+N (and >= today
        # so we don't double-count overdue rows).
        due_within = params.get("due_within")
        if due_within:
            try:
                n = int(due_within)
                today = timezone.now().date()
                qs = qs.filter(
                    status__in=Invoice.UNPAID_ACTIVE_STATUSES,
                    due_date__gte=today,
                    due_date__lte=today + timedelta(days=n),
                )
            except (TypeError, ValueError):
                pass

        return qs

    def _check_role(self):
        role = getattr(self.request.user, "role", None)
        if role not in _FINANCE_ROLES:
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        return None

    def retrieve(self, request, *args, **kwargs):
        denied = self._check_role()
        if denied:
            return denied
        return super().retrieve(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        denied = self._check_role()
        if denied:
            return denied
        qs = self.get_queryset().order_by("due_date", "-created_at")
        page = self.paginate_queryset(qs)
        if page is not None:
            ser = self.get_serializer(page, many=True)
            return self.get_paginated_response(ser.data)
        ser = self.get_serializer(qs, many=True)

        # Tiny rollup so the Finance widget can render counters without
        # an extra round-trip.
        totals = qs.aggregate(total=Sum("amount"))
        return Response(
            {
                "results": ser.data,
                "summary": {
                    "count": qs.count(),
                    "total_amount": str(totals["total"] or 0),
                    "open_count": qs.filter(status=Invoice.STATUS_OPEN).count(),
                    "overdue_count": qs.filter(
                        status=Invoice.STATUS_OPEN,
                        due_date__lt=timezone.now().date(),
                    ).count(),
                },
            }
        )

    def create(self, request, *args, **kwargs):
        denied = self._check_role()
        if denied:
            return denied
        user = request.user
        restaurant = getattr(user, "restaurant", None)
        if restaurant is None:
            return Response(
                {"error": "User has no associated restaurant"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        invoice = ser.save(restaurant=restaurant, created_by=user)
        try:
            from finance.audit import InvoiceAuditEvent, log_invoice_event

            log_invoice_event(
                invoice,
                InvoiceAuditEvent.EVENT_CREATED,
                actor=user,
                channel=InvoiceAuditEvent.CHANNEL_DASHBOARD,
                summary=f"Invoice created — {invoice.vendor_name}, {invoice.amount} {invoice.currency}.",
            )
        except Exception:
            logger.exception("audit log failed for invoice create")
        return Response(ser.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        denied = self._check_role()
        if denied:
            return denied
        invoice = self.get_object()
        response = super().update(request, *args, **kwargs)
        if response.status_code < 400:
            try:
                from finance.audit import InvoiceAuditEvent, log_invoice_event

                log_invoice_event(
                    invoice,
                    InvoiceAuditEvent.EVENT_DATA_EDITED,
                    actor=request.user,
                    channel=InvoiceAuditEvent.CHANNEL_DASHBOARD,
                    summary=f"Invoice data updated by dashboard.",
                    metadata={"fields": list(request.data.keys()) if isinstance(request.data, dict) else []},
                )
            except Exception:
                logger.exception("audit log failed for invoice update")
        return response

    def partial_update(self, request, *args, **kwargs):
        denied = self._check_role()
        if denied:
            return denied
        invoice = self.get_object()
        response = super().partial_update(request, *args, **kwargs)
        if response.status_code < 400:
            try:
                from finance.audit import InvoiceAuditEvent, log_invoice_event

                log_invoice_event(
                    invoice,
                    InvoiceAuditEvent.EVENT_DATA_EDITED,
                    actor=request.user,
                    channel=InvoiceAuditEvent.CHANNEL_DASHBOARD,
                    summary="Invoice data updated by dashboard.",
                    metadata={"fields": list(request.data.keys()) if isinstance(request.data, dict) else []},
                )
            except Exception:
                logger.exception("audit log failed for invoice partial_update")
        return response

    def destroy(self, request, *args, **kwargs):
        """Soft-delete by setting status=VOIDED (preserves audit trail)."""
        denied = self._check_role()
        if denied:
            return denied
        invoice = self.get_object()
        invoice.status = Invoice.STATUS_VOIDED
        invoice.save(update_fields=["status", "updated_at"])
        try:
            from finance.audit import InvoiceAuditEvent, log_invoice_event

            log_invoice_event(
                invoice,
                InvoiceAuditEvent.EVENT_VOIDED,
                actor=request.user,
                channel=InvoiceAuditEvent.CHANNEL_DASHBOARD,
                summary="Invoice voided.",
            )
        except Exception:
            logger.exception("audit log failed for invoice void")
        return Response({"success": True, "id": str(invoice.id), "status": invoice.status})

    @action(detail=True, methods=["post"], url_path="mark-paid")
    def mark_paid(self, request, pk=None):
        denied = self._check_role()
        if denied:
            return denied
        invoice = self.get_object()
        if invoice.status == Invoice.STATUS_PAID:
            return Response(
                {"success": True, "message": "Invoice already marked paid", "invoice": InvoiceSerializer(invoice).data}
            )

        from finance.payment_approval import payment_allowed, start_payment_approval

        ok, block_msg = payment_allowed(invoice)
        if not ok:
            if invoice.approval_status == Invoice.APPROVAL_NONE:
                start_payment_approval(invoice=invoice, requested_by=request.user)
                invoice.refresh_from_db()
                ok, block_msg = payment_allowed(invoice)
            if not ok:
                return Response(
                    {"success": False, "error": "approval_required", "message": block_msg},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        raw_paid_on = request.data.get("paid_on") or request.data.get("paid_at")
        paid_on = None
        if raw_paid_on:
            paid_on = parse_datetime(str(raw_paid_on)) or parse_date(str(raw_paid_on))

        method = str(request.data.get("payment_method") or request.data.get("method") or "").upper()
        reference = str(request.data.get("payment_reference") or request.data.get("reference") or "")
        amount = request.data.get("amount")

        invoice.mark_paid(
            paid_on=paid_on,
            method=method,
            reference=reference,
            amount=amount,
            user=request.user,
        )
        try:
            from finance.audit import InvoiceAuditEvent, log_invoice_event

            log_invoice_event(
                invoice,
                InvoiceAuditEvent.EVENT_PAYMENT_RECORDED,
                actor=request.user,
                channel=InvoiceAuditEvent.CHANNEL_DASHBOARD,
                summary=(
                    f"Payment recorded — {invoice.amount} {invoice.currency}"
                    + (f" via {method}" if method else "")
                    + "."
                ),
                metadata={
                    "payment_method": method,
                    "payment_reference": reference,
                },
            )
        except Exception:
            logger.exception("audit log failed for mark_paid")
        return Response({"success": True, "invoice": InvoiceSerializer(invoice).data})

    @action(detail=True, methods=["get"], url_path="timeline")
    def timeline(self, request, pk=None):
        denied = self._check_role()
        if denied:
            return denied
        invoice = self.get_object()
        from finance.timeline import build_invoice_timeline, summarize_timeline_for_miya

        events = build_invoice_timeline(invoice)
        return Response(
            {
                "success": True,
                "invoice_id": str(invoice.id),
                "events": events,
                "summary": summarize_timeline_for_miya(invoice, events),
            }
        )

    @action(detail=True, methods=["post"], url_path="proof-of-payment")
    def proof_of_payment(self, request, pk=None):
        """Upload proof-of-payment file for a paid or approved invoice."""
        denied = self._check_role()
        if denied:
            return denied
        invoice = self.get_object()
        uploaded = request.FILES.get("proof_of_payment") or request.FILES.get("file")
        if not uploaded:
            return Response(
                {"success": False, "error": "proof_of_payment file required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            invoice.proof_of_payment.save(uploaded.name, uploaded, save=True)
        except Exception:
            logger.exception("proof_of_payment upload failed")
            return Response(
                {"success": False, "error": "upload_failed"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            from finance.audit import InvoiceAuditEvent, log_invoice_event

            log_invoice_event(
                invoice,
                InvoiceAuditEvent.EVENT_PROOF_UPLOADED,
                actor=request.user,
                channel=InvoiceAuditEvent.CHANNEL_DASHBOARD,
                summary=f"Proof of payment uploaded for {invoice.vendor_name}.",
            )
        except Exception:
            logger.exception("audit log failed for proof upload")
        return Response({"success": True, "invoice": InvoiceSerializer(invoice).data})
