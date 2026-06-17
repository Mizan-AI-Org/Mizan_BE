"""Tests for payroll agent endpoints (P1 Morocco compliance wedge)."""

from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory

from accounts.models import CustomUser, Restaurant
from finance.models import Invoice
from payroll.models import ComplianceReminder, TemperatureReading
from payroll.views_agent import (
    agent_compliance_reminders,
    agent_generate_payslips,
    agent_log_temperature,
    agent_sync_delivery_menu,
)


@override_settings(LUA_WEBHOOK_API_KEY="test-agent-key")
class PayrollAgentViewsTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.restaurant = Restaurant.objects.create(name="Test Bistro", slug="test-bistro-payroll")
        self.user = CustomUser.objects.create_user(
            email="mgr@test.com",
            password="pass12345",
            restaurant=self.restaurant,
            first_name="Karim",
            last_name="Benali",
        )

    def _auth_headers(self):
        return {"HTTP_AUTHORIZATION": "Bearer test-agent-key"}

    def test_log_temperature_creates_reading(self):
        request = self.factory.post(
            "/api/payroll/agent/temperature-log/",
            {
                "restaurant_id": str(self.restaurant.id),
                "equipment": "walk-in cooler",
                "value_c": "4.2",
            },
            format="json",
            **self._auth_headers(),
        )
        response = agent_log_temperature(request)
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["success"])
        self.assertEqual(TemperatureReading.objects.filter(restaurant=self.restaurant).count(), 1)
        reading = TemperatureReading.objects.get()
        self.assertEqual(reading.equipment, "walk-in cooler")
        self.assertEqual(reading.value_c, Decimal("4.2"))

    def test_seed_compliance_reminders_is_idempotent(self):
        payload = {"restaurant_id": str(self.restaurant.id)}

        first = self.factory.post(
            "/api/payroll/agent/compliance-reminders/seed/",
            payload,
            format="json",
            **self._auth_headers(),
        )
        response = agent_compliance_reminders(first)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        created_first = response.data["created"]
        self.assertGreater(created_first, 0)

        second = self.factory.post(
            "/api/payroll/agent/compliance-reminders/seed/",
            payload,
            format="json",
            **self._auth_headers(),
        )
        response2 = agent_compliance_reminders(second)
        self.assertEqual(response2.data["created"], 0)
        self.assertEqual(
            ComplianceReminder.objects.filter(restaurant=self.restaurant).count(),
            created_first,
        )

    def test_generate_payslip_for_staff(self):
        request = self.factory.post(
            "/api/payroll/agent/payslips/generate/",
            {
                "restaurant_id": str(self.restaurant.id),
                "staff_name": "Karim Benali",
                "month": 3,
                "year": 2026,
            },
            format="json",
            **self._auth_headers(),
        )
        response = agent_generate_payslips(request)
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["success"])
        self.assertGreaterEqual(response.data["count"], 1)

    def test_sync_delivery_menu_with_no_items(self):
        request = self.factory.post(
            "/api/payroll/agent/delivery-menu/sync/",
            {"restaurant_id": str(self.restaurant.id), "provider": "GLOVO"},
            format="json",
            **self._auth_headers(),
        )
        response = agent_sync_delivery_menu(request)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["item_count"], 0)


@override_settings(LUA_WEBHOOK_API_KEY="test-agent-key")
class FinanceBankPaymentStatusTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.restaurant = Restaurant.objects.create(name="Finance Bistro", slug="finance-bistro")
        self.user = CustomUser.objects.create_user(
            email="finance@test.com",
            password="pass12345",
            restaurant=self.restaurant,
        )
        self.invoice = Invoice.objects.create(
            restaurant=self.restaurant,
            vendor_name="Boulanger",
            invoice_number="878789",
            amount=Decimal("4000.00"),
            due_date="2026-07-30",
        )

    def test_bank_payment_cleared_marks_invoice_paid(self):
        from finance.views_agent import agent_update_invoice_bank_payment_status

        request = self.factory.post(
            "/api/finance/agent/invoices/payment-status/",
            {
                "restaurant_id": str(self.restaurant.id),
                "vendor": "Boulanger",
                "invoice_number": "878789",
                "bank_payment_status": "CLEARED",
            },
            format="json",
            HTTP_AUTHORIZATION="Bearer test-agent-key",
        )
        response = agent_update_invoice_bank_payment_status(request)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.bank_payment_status, Invoice.BANK_PAYMENT_CLEARED)
        self.assertEqual(self.invoice.status, Invoice.STATUS_PAID)
