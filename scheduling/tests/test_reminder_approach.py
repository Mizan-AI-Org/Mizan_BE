"""Tests for compliance-linked personal reminder sync and approach nudges."""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from accounts.models import CustomUser, Restaurant
from payroll.models import ComplianceDocument
from payroll.services.compliance_reminder_sync import (
    _local_expiry_datetime,
    sync_compliance_document_reminder,
)
from scheduling.memory_models import PersonalReminder
from scheduling.memory_tasks import personal_reminder_approach_sweep
from scheduling.reminder_messaging import next_approach_milestone


class ComplianceReminderSyncTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Test Resto", slug="test-resto")
        self.owner = CustomUser.objects.create_user(
            username="owner1",
            email="owner@test.com",
            password="pass",
            role="OWNER",
            restaurant=self.restaurant,
            phone="+212600000001",
        )

    def test_sync_creates_expiration_reminder(self):
        doc = ComplianceDocument.objects.create(
            restaurant=self.restaurant,
            title="Business insurance",
            document_type=ComplianceDocument.TYPE_INSURANCE,
            expires_at=date(2026, 8, 7),
            remind_days_before=30,
            created_by=self.owner,
        )
        summary = sync_compliance_document_reminder(doc, owner=self.owner)
        self.assertEqual(summary["created"], 1)
        rem = PersonalReminder.objects.get(linked_compliance_document=doc)
        self.assertEqual(rem.title, "Business insurance Expiration Reminder")
        self.assertEqual(rem.due_at, _local_expiry_datetime(date(2026, 8, 7)))
        self.assertEqual(rem.status, "pending")

    def test_sync_updates_existing_reminder(self):
        doc = ComplianceDocument.objects.create(
            restaurant=self.restaurant,
            title="Business registration",
            document_type=ComplianceDocument.TYPE_BUSINESS_REGISTRATION,
            expires_at=date(2026, 12, 31),
            remind_days_before=60,
            created_by=self.owner,
        )
        sync_compliance_document_reminder(doc, owner=self.owner)
        doc.expires_at = date(2027, 1, 15)
        doc.save()
        summary = sync_compliance_document_reminder(doc, owner=self.owner, reset_nudges=True)
        self.assertEqual(summary["updated"], 1)
        rem = PersonalReminder.objects.get(linked_compliance_document=doc)
        self.assertEqual(rem.due_at.date(), date(2027, 1, 15))
        self.assertEqual(rem.approach_nudges_sent, [])


class ReminderApproachSweepTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Ping Resto", slug="ping-resto")
        self.owner = CustomUser.objects.create_user(
            username="mgr1",
            email="mgr@test.com",
            password="pass",
            role="MANAGER",
            restaurant=self.restaurant,
            phone="+212600000099",
        )

    def test_next_milestone_one_day_before(self):
        tomorrow = timezone.localdate() + timedelta(days=1)
        rem = PersonalReminder(
            title="Insurance Expiration Reminder",
            due_at=_local_expiry_datetime(tomorrow),
            approach_nudges_sent=[],
        )
        self.assertEqual(next_approach_milestone(rem), 1)

    @patch("notifications.services.notification_service.send_whatsapp_text", return_value=(True, None))
    def test_approach_sweep_sends_whatsapp(self, mock_send):
        tomorrow = timezone.localdate() + timedelta(days=1)
        doc = ComplianceDocument.objects.create(
            restaurant=self.restaurant,
            title="Insurance",
            document_type=ComplianceDocument.TYPE_INSURANCE,
            expires_at=tomorrow,
            remind_days_before=30,
            created_by=self.owner,
        )
        PersonalReminder.objects.create(
            restaurant=self.restaurant,
            owner=self.owner,
            phone="212600000099",
            title="Insurance Expiration Reminder",
            due_at=_local_expiry_datetime(tomorrow),
            linked_compliance_document=doc,
            approach_nudges_sent=[],
        )
        result = personal_reminder_approach_sweep()
        self.assertEqual(result["sent"], 1)
        mock_send.assert_called_once()
        rem = PersonalReminder.objects.get(linked_compliance_document=doc)
        self.assertIn(1, rem.approach_nudges_sent)
