"""PayGuard threshold routing smoke tests."""

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from accounts.models import CustomUser, Restaurant
from finance.models import Invoice
from finance.payment_approval import (
    SETTINGS_KEY,
    get_policy,
    resolve_tier,
    start_payment_approval,
)


class InvoiceThresholdRoutingTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Threshold Bistro")
        self.owner = CustomUser.objects.create_user(
            email="owner@th.com",
            password="pass12345",
            restaurant=self.restaurant,
            role="OWNER",
            first_name="Omar",
            last_name="Owner",
        )
        self.approver = CustomUser.objects.create_user(
            email="approver@th.com",
            password="pass12345",
            restaurant=self.restaurant,
            role="MANAGER",
            first_name="Sara",
            last_name="Finance",
        )
        gs = dict(self.restaurant.general_settings or {})
        gs[SETTINGS_KEY] = {
            "enabled": True,
            "currency": "MAD",
            "tiers": [
                {
                    "currency": "MAD",
                    "max_amount": "1000",
                    "steps": [{"role": "MANAGER", "label": "Manager"}],
                },
                {
                    "currency": "MAD",
                    "max_amount": None,
                    "steps": [
                        {
                            "user_id": str(self.approver.id),
                            "label": "Finance approver",
                        }
                    ],
                },
            ],
        }
        self.restaurant.general_settings = gs
        self.restaurant.save(update_fields=["general_settings"])

    def _invoice(self, amount: str) -> Invoice:
        return Invoice.objects.create(
            restaurant=self.restaurant,
            vendor_name="Sysco",
            invoice_number=f"INV-{amount}",
            amount=Decimal(amount),
            currency="MAD",
            due_date="2026-08-15",
            status="OPEN",
            created_by=self.owner,
        )

    def test_below_threshold_resolves_first_tier(self):
        inv = self._invoice("500.00")
        policy = get_policy(self.restaurant)
        tier = resolve_tier(policy, inv.amount, currency="MAD")
        self.assertIsNotNone(tier)
        self.assertEqual(str(tier.get("max_amount")), "1000")

    def test_above_threshold_resolves_open_ended_tier(self):
        inv = self._invoice("1500.00")
        policy = get_policy(self.restaurant)
        tier = resolve_tier(policy, inv.amount, currency="MAD")
        self.assertIsNotNone(tier)
        self.assertTrue(tier.get("max_amount") in (None, "", "null") or _is_open(tier))
        result = start_payment_approval(invoice=inv, requested_by=self.owner)
        self.assertTrue(result.get("success"))
        inv.refresh_from_db()
        self.assertIn(
            inv.approval_status,
            ("PENDING", "IN_PROGRESS", "APPROVED", "NOT_REQUIRED", ""),
        )

    def test_equal_threshold_stays_in_first_band(self):
        inv = self._invoice("1000.00")
        policy = get_policy(self.restaurant)
        tier = resolve_tier(policy, inv.amount, currency="MAD")
        self.assertIsNotNone(tier)
        self.assertEqual(str(tier.get("max_amount")), "1000")


def _is_open(tier: dict) -> bool:
    max_a = tier.get("max_amount")
    return max_a is None or max_a in ("", "null", "Infinity", "inf")
