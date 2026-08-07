"""Tests for Settings compliance documents API (JSON body support)."""

from __future__ import annotations

import json
from datetime import date

from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import CustomUser, Restaurant
from payroll.models import ComplianceDocument
from scheduling.memory_models import PersonalReminder


class ComplianceDocumentViewSetTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Doc Resto", slug="doc-resto")
        self.manager = CustomUser.objects.create_user(
            username="mgr-docs",
            email="mgr-docs@test.com",
            password="pass",
            role="MANAGER",
            restaurant=self.restaurant,
            phone="+212600000011",
        )
        self.client = APIClient()
        token = str(AccessToken.for_user(self.manager))
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_create_document_with_json_body(self):
        response = self.client.post(
            "/api/payroll/compliance-documents/",
            data=json.dumps(
                {
                    "title": "Test insurance",
                    "document_type": "INSURANCE",
                    "expires_at": "2026-08-30",
                    "remind_days_before": 30,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()
        self.assertTrue(payload.get("success"))
        doc_id = payload["document"]["id"]
        doc = ComplianceDocument.objects.get(id=doc_id)
        self.assertEqual(doc.title, "Test insurance")
        self.assertEqual(doc.expires_at, date(2026, 8, 30))
        rem = PersonalReminder.objects.filter(linked_compliance_document=doc).first()
        self.assertIsNotNone(rem)
        self.assertEqual(rem.status, "pending")

    def test_patch_expiry_with_json_body(self):
        doc = ComplianceDocument.objects.create(
            restaurant=self.restaurant,
            title="Business insurance",
            document_type=ComplianceDocument.TYPE_INSURANCE,
            created_by=self.manager,
        )
        response = self.client.patch(
            f"/api/payroll/compliance-documents/{doc.id}/",
            data=json.dumps({"expires_at": "2026-09-15"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        doc.refresh_from_db()
        self.assertEqual(doc.expires_at, date(2026, 9, 15))
        self.assertTrue(
            PersonalReminder.objects.filter(linked_compliance_document=doc, status="pending").exists()
        )
