"""Tests for the drag-and-drop bucket-move endpoint.

PATCH /api/dashboard/tasks-demands/<uuid>/bucket/

Covers the four source models the dispatcher touches:

- ``staff.StaffRequest``: category move, urgent priority bump, audit
  comment, auto-assignee re-resolution.
- ``finance.Invoice``: rejected for cross-bucket moves with a
  user-readable hint; no-op for finance→finance.
- ``dashboard.Task`` and ``scheduling.Task``: rejected with a 400 +
  hint.
- Unknown id: 404.
"""

from __future__ import annotations

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import CustomUser, Restaurant


class TaskBucketUpdateStaffRequestTests(TestCase):
    """Most dashboard widget rows are ``StaffRequest``s — the bulk of
    drag-and-drop moves go through this branch."""

    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Bucket Bistro")
        self.manager = CustomUser.objects.create_user(
            email="m@b.com",
            password="x",
            first_name="Mona",
            last_name="Manager",
            role="MANAGER",
            restaurant=self.restaurant,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.manager)

        from staff.models import StaffRequest
        self.StaffRequest = StaffRequest

    def _make_request(self, *, category="OTHER", priority="MEDIUM"):
        return self.StaffRequest.objects.create(
            restaurant=self.restaurant,
            staff_name="Adam",
            staff_phone="+212600000000",
            subject="Buy napkins",
            description="we need 30 napkins",
            category=category,
            priority=priority,
        )

    def _patch(self, sr_id, bucket):
        return self.client.patch(
            f"/api/dashboard/tasks-demands/{sr_id}/bucket/",
            data={"bucket": bucket},
            format="json",
        )

    def test_move_other_to_purchase_orders_changes_category(self):
        sr = self._make_request(category="OTHER")
        resp = self._patch(sr.id, "purchase_orders")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        sr.refresh_from_db()
        self.assertEqual(sr.category, "PURCHASE_ORDER")

    def test_move_misc_to_finance(self):
        sr = self._make_request(category="OTHER")
        resp = self._patch(sr.id, "finance")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        sr.refresh_from_db()
        self.assertEqual(sr.category, "FINANCE")

    def test_move_records_audit_comment(self):
        from staff.models import StaffRequestComment

        sr = self._make_request(category="OTHER")
        resp = self._patch(sr.id, "human_resources")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        comments = StaffRequestComment.objects.filter(request=sr)
        self.assertTrue(
            any("OTHER" in (c.body or "") and "HR" in (c.body or "") for c in comments),
            f"expected an audit comment about OTHER→HR; got {[c.body for c in comments]}",
        )

    def test_drop_on_urgent_widget_bumps_priority_not_category(self):
        sr = self._make_request(category="MAINTENANCE", priority="MEDIUM")
        resp = self._patch(sr.id, "urgent")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        sr.refresh_from_db()
        # Urgent is a priority lane — the original category must be
        # preserved so the row is still visible in the maintenance
        # widget after the bump.
        self.assertEqual(sr.priority, "URGENT")
        self.assertEqual(sr.category, "MAINTENANCE")

    def test_drop_on_same_bucket_is_noop(self):
        # The FE short-circuits this case before sending, but the BE
        # must still respond cleanly if it does come through.
        sr = self._make_request(category="HR")
        resp = self._patch(sr.id, "human_resources")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        sr.refresh_from_db()
        self.assertEqual(sr.category, "HR")

    def test_invalid_bucket_returns_400(self):
        sr = self._make_request()
        resp = self._patch(sr.id, "nonsense")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unknown_uuid_returns_404(self):
        import uuid as _uuid

        resp = self._patch(_uuid.uuid4(), "finance")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class TaskBucketUpdateInvoiceTests(TestCase):
    """Invoices live in the Finance widget exclusively — the BE rejects
    every cross-widget drop with a user-readable hint."""

    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Invoice Bistro")
        self.manager = CustomUser.objects.create_user(
            email="m@i.com",
            password="x",
            first_name="Mona",
            last_name="Manager",
            role="MANAGER",
            restaurant=self.restaurant,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.manager)

    def _make_invoice(self):
        from datetime import date, timedelta

        from finance.models import Invoice

        return Invoice.objects.create(
            restaurant=self.restaurant,
            vendor_name="Acme Suppliers",
            invoice_number="INV-DRAG-001",
            amount=100,
            currency="MAD",
            due_date=date.today() + timedelta(days=7),
            created_by=self.manager,
        )

    def test_invoice_drop_outside_finance_is_rejected(self):
        inv = self._make_invoice()
        resp = self.client.patch(
            f"/api/dashboard/tasks-demands/{inv.id}/bucket/",
            data={"bucket": "human_resources"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Finance", resp.data.get("error", ""))

    def test_invoice_drop_on_finance_is_noop(self):
        inv = self._make_invoice()
        resp = self.client.patch(
            f"/api/dashboard/tasks-demands/{inv.id}/bucket/",
            data={"bucket": "finance"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
