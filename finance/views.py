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
        if st in {Invoice.STATUS_DRAFT, Invoice.STATUS_OPEN, Invoice.STATUS_PAID, Invoice.STATUS_VOIDED}:
            qs = qs.filter(status=st)

        vendor = (params.get("vendor") or "").strip()
        if vendor:
            qs = qs.filter(vendor_name__icontains=vendor)

        # ``overdue=true`` — open + due_date < today.
        overdue = (params.get("overdue") or "").lower() in ("true", "1", "yes")
        if overdue:
            qs = qs.filter(status=Invoice.STATUS_OPEN, due_date__lt=timezone.now().date())

        # ``due_within=N`` days — open + due_date <= today+N (and >= today
        # so we don't double-count overdue rows).
        due_within = params.get("due_within")
        if due_within:
            try:
                n = int(due_within)
                today = timezone.now().date()
                qs = qs.filter(
                    status=Invoice.STATUS_OPEN,
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
        ser.save(restaurant=restaurant, created_by=user)
        return Response(ser.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        denied = self._check_role()
        if denied:
            return denied
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        denied = self._check_role()
        if denied:
            return denied
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        """Soft-delete by setting status=VOIDED (preserves audit trail)."""
        denied = self._check_role()
        if denied:
            return denied
        invoice = self.get_object()
        invoice.status = Invoice.STATUS_VOIDED
        invoice.save(update_fields=["status", "updated_at"])
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
        return Response({"success": True, "invoice": InvoiceSerializer(invoice).data})
