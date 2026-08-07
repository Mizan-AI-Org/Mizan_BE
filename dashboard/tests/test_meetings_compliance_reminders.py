"""Compliance expiry rows appear in Meetings & Reminders widget."""

from __future__ import annotations

from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import CustomUser, Restaurant
from payroll.models import ComplianceDocument
from payroll.services.compliance_reminder_sync import _local_expiry_datetime, sync_compliance_document_reminder


class MeetingsComplianceReminderTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Meet Resto", slug="meet-resto")
        self.manager = CustomUser.objects.create_user(
            username="mgr-meet",
            email="mgr-meet@test.com",
            password="pass",
            role="MANAGER",
            restaurant=self.restaurant,
            phone="+212600000022",
        )
        self.client = APIClient()
        token = str(AccessToken.for_user(self.manager))
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_compliance_reminder_shows_when_gcal_not_connected(self):
        expires = timezone.localdate() + timedelta(days=10)
        doc = ComplianceDocument.objects.create(
            restaurant=self.restaurant,
            title="Health permit",
            document_type=ComplianceDocument.TYPE_HEALTH_PERMIT,
            expires_at=expires,
            remind_days_before=30,
            created_by=self.manager,
        )
        sync_compliance_document_reminder(doc, owner=self.manager)

        response = self.client.get("/api/dashboard/meetings-reminders/?limit=6")
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertFalse(payload["connected"])
        self.assertGreaterEqual(payload["counts"]["urgent"], 1)
        titles = [row["title"] for row in payload["items"]]
        self.assertTrue(any("Health permit" in title for title in titles))

    def test_compliance_reminder_hidden_outside_remind_window(self):
        expires = timezone.localdate() + timedelta(days=90)
        doc = ComplianceDocument.objects.create(
            restaurant=self.restaurant,
            title="Far away license",
            document_type=ComplianceDocument.TYPE_OTHER,
            expires_at=expires,
            remind_days_before=30,
            created_by=self.manager,
        )
        sync_compliance_document_reminder(doc, owner=self.manager)

        response = self.client.get("/api/dashboard/meetings-reminders/?limit=6")
        payload = response.json()
        titles = [row["title"] for row in payload.get("items", [])]
        self.assertFalse(any("Far away license" in title for title in titles))
