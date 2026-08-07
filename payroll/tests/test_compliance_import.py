"""Tests for compliance document import from parsed uploads."""

from __future__ import annotations

from datetime import date

from django.test import TestCase

from accounts.models import CustomUser, Restaurant
from payroll.models import ComplianceDocument
from payroll.services.compliance_import import (
    infer_document_type,
    parse_remind_days_before,
    should_track_as_restaurant_compliance,
    try_create_compliance_from_classification,
)
from scheduling.memory_models import PersonalReminder


class ComplianceImportTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Import Resto", slug="import-resto")
        self.manager = CustomUser.objects.create_user(
            username="mgr-import",
            email="mgr-import@test.com",
            password="pass",
            role="MANAGER",
            restaurant=self.restaurant,
            phone="+212600000099",
        )
        self.staff = CustomUser.objects.create_user(
            username="staff-import",
            email="staff-import@test.com",
            password="pass",
            role="STAFF",
            restaurant=self.restaurant,
            phone="+212600000088",
        )

    def test_parse_remind_days_before_weeks(self):
        self.assertEqual(parse_remind_days_before("Remind me 2 weeks before expiry"), 14)
        self.assertEqual(parse_remind_days_before("Rappelle-moi 2 semaines avant l'expiration"), 14)
        self.assertEqual(parse_remind_days_before("notify 1 month before renewal"), 30)

    def test_infer_document_type_insurance(self):
        self.assertEqual(
            infer_document_type({"document_type": "Business liability insurance"}, "Policy"),
            "INSURANCE",
        )

    def test_should_track_manager_insurance_caption(self):
        classification = {
            "category": "id_or_certification",
            "confidence": 0.8,
            "fields": {
                "document_type": "Business insurance",
                "expiry_date": "2027-07-29",
            },
        }
        self.assertTrue(
            should_track_as_restaurant_compliance(
                classification=classification,
                note="Remind me 2 weeks before expiry so I can renew my insurance",
                acting_user=self.manager,
            )
        )
        self.assertFalse(
            should_track_as_restaurant_compliance(
                classification=classification,
                note="Remind me 2 weeks before expiry",
                acting_user=self.staff,
            )
        )

    def test_try_create_compliance_updates_starter_insurance(self):
        starter = ComplianceDocument.objects.create(
            restaurant=self.restaurant,
            title="Business insurance",
            document_type=ComplianceDocument.TYPE_INSURANCE,
        )
        classification = {
            "category": "id_or_certification",
            "confidence": 0.82,
            "summary": "Restaurant liability insurance policy",
            "fields": {
                "document_type": "Insurance policy",
                "expiry_date": "2027-07-29",
            },
        }
        doc, msg = try_create_compliance_from_classification(
            restaurant=self.restaurant,
            acting_user=self.manager,
            classification=classification,
            file_bytes=b"fake-pdf-bytes",
            filename="insurance.pdf",
            content_type="application/pdf",
            note="Remind me 2 weeks before expiry so I can renew my insurance",
        )
        self.assertIsNotNone(doc)
        self.assertEqual(doc.id, starter.id)
        self.assertEqual(doc.expires_at, date(2027, 7, 29))
        self.assertEqual(doc.remind_days_before, 14)
        self.assertTrue(doc.file.name)
        self.assertIn("2027-07-29", msg)
        rem = PersonalReminder.objects.filter(linked_compliance_document=doc, status="pending").first()
        self.assertIsNotNone(rem)
        self.assertEqual(rem.owner_id, self.manager.id)

    def test_staff_id_not_tracked_without_business_signals(self):
        classification = {
            "category": "id_or_certification",
            "confidence": 0.9,
            "fields": {
                "document_type": "Food handler card",
                "person_name": "Ahmed Benali",
                "expiry_date": "2026-12-01",
            },
        }
        doc, msg = try_create_compliance_from_classification(
            restaurant=self.restaurant,
            acting_user=self.manager,
            classification=classification,
            note="",
        )
        self.assertIsNone(doc)
        self.assertEqual(msg, "")
