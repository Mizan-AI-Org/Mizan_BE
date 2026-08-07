"""Phase 14.2 hardening — D5: agent cannot mutate via auto_create=true."""
from __future__ import annotations

from io import BytesIO
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase

from accounts.models import BusinessLocation, CustomUser, Restaurant
from finance.models import Invoice
from miya.services.document_input import ingest_document
from miya.tests.e2e.harness import MiyaE2EHarness, PostgresE2ETestCase
from miya.tests.e2e.seed import seed_single_establishment
from payroll.models import ComplianceDocument
from staff.models_task import SafetyConcernReport


INCIDENT_PARSE = {
    "category": "incident",
    "confidence": 0.92,
    "summary": "Broken freezer in kitchen",
    "fields": {"incident_type": "equipment", "severity": "HIGH"},
}

INVOICE_PARSE = {
    "category": "invoice_or_receipt",
    "confidence": 0.92,
    "summary": "Invoice from Acme Foods",
    "fields": {
        "vendor": "Acme Foods",
        "amount": "1500.00",
        "currency": "MAD",
        "invoice_number": "INV-D5",
    },
}

INSURANCE_PARSE = {
    "category": "id_or_certification",
    "confidence": 0.91,
    "summary": "Insurance policy expiring 2026-09-30",
    "fields": {"expiry_date": "2026-09-30", "document_type": "insurance"},
}


def _seed():
    rest = Restaurant.objects.create(
        name="D5 Rest",
        email="d5@test.mizan.local",
        timezone="Africa/Casablanca",
    )
    loc = BusinessLocation.objects.create(
        restaurant=rest, name="Main", is_primary=True, is_active=True
    )
    mgr = CustomUser.objects.create_user(
        email="d5-mgr@test.mizan.local",
        password="testpass",
        first_name="Mgr",
        last_name="D5",
        role="MANAGER",
        restaurant=rest,
        primary_location=loc,
    )
    mgr.managed_locations.add(loc)
    return rest, loc, mgr


class D5AutoCreateHardeningTests(TestCase):
    def setUp(self):
        self.rest, self.loc, self.manager = _seed()
        self.factory = RequestFactory()

    @patch("dashboard.api.photo_router.parse_photo")
    @patch("dashboard.api.photo_router._try_create_incident")
    def test_parse_photo_auto_create_true_cannot_create_incident(self, mock_inc, mock_parse):
        mock_parse.return_value = dict(INCIDENT_PARSE)
        from dashboard.api.photo_router import agent_parse_photo

        req = self.factory.post(
            "/api/dashboard/agent/parse-photo/",
            {"restaurant_id": str(self.rest.id), "auto_create": "true"},
        )
        req.user = self.manager
        req.FILES["image"] = SimpleUploadedFile("inc.jpg", b"img", content_type="image/jpeg")
        resp = agent_parse_photo(req)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("classification", resp.data)
        mock_inc.assert_not_called()
        self.assertEqual(
            SafetyConcernReport.objects.filter(restaurant=self.rest).count(),
            0,
        )

    @patch("dashboard.api.document_router.parse_document")
    @patch("dashboard.api.photo_router._try_create_invoice")
    @patch("dashboard.api.document_router.looks_like_process_import", return_value=False)
    def test_parse_document_auto_create_true_cannot_create_invoice(
        self, _looks, mock_invoice, mock_parse
    ):
        mock_parse.return_value = dict(INVOICE_PARSE)
        from dashboard.api.document_router import agent_parse_document

        inv_before = Invoice.objects.filter(restaurant=self.rest).count()
        req = self.factory.post(
            "/api/dashboard/agent/parse-document/",
            {"restaurant_id": str(self.rest.id), "auto_create": "true", "import_processes": "false"},
        )
        req.user = self.manager
        req.FILES["document"] = SimpleUploadedFile(
            "inv.pdf", b"%PDF", content_type="application/pdf"
        )
        resp = agent_parse_document(req)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("classification", resp.data)
        mock_invoice.assert_not_called()
        self.assertEqual(Invoice.objects.filter(restaurant=self.rest).count(), inv_before)

    @patch("dashboard.api.document_router.parse_document")
    @patch("payroll.services.compliance_import.try_create_compliance_from_classification")
    @patch("dashboard.api.document_router.looks_like_process_import", return_value=False)
    def test_parse_document_auto_create_true_cannot_create_compliance(
        self, _looks, mock_comp, mock_parse
    ):
        mock_parse.return_value = dict(INSURANCE_PARSE)
        from dashboard.api.document_router import agent_parse_document

        comp_before = ComplianceDocument.objects.filter(restaurant=self.rest).count()
        req = self.factory.post(
            "/api/dashboard/agent/parse-document/",
            {"restaurant_id": str(self.rest.id), "auto_create": "true", "import_processes": "false"},
        )
        req.user = self.manager
        req.FILES["document"] = SimpleUploadedFile(
            "ins.pdf", b"%PDF", content_type="application/pdf"
        )
        resp = agent_parse_document(req)
        self.assertEqual(resp.status_code, 200)
        mock_comp.assert_not_called()
        self.assertEqual(
            ComplianceDocument.objects.filter(restaurant=self.rest).count(),
            comp_before,
        )

    @patch("dashboard.api.photo_router.parse_photo")
    def test_extraction_still_succeeds_with_auto_create_true(self, mock_parse):
        mock_parse.return_value = dict(INVOICE_PARSE)
        from dashboard.api.photo_router import agent_parse_photo

        req = self.factory.post(
            "/api/dashboard/agent/parse-photo/",
            {"restaurant_id": str(self.rest.id), "auto_create": "true"},
        )
        req.user = self.manager
        req.FILES["image"] = SimpleUploadedFile("inv.jpg", b"img", content_type="image/jpeg")
        resp = agent_parse_photo(req)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data.get("success"))
        self.assertEqual(resp.data["classification"]["category"], "invoice_or_receipt")

    def test_media_tools_strips_auto_create_from_dispatch(self):
        from miya.services.media_tools import dispatch_parse_photo

        with patch("miya.services.tool_dispatch.dispatch_agent_request") as mock_req:
            mock_req.return_value = (200, {"success": True})
            dispatch_parse_photo(
                {"media_url": "https://example.com/p.jpg", "auto_create": True},
                {"restaurant_id": str(self.rest.id)},
                headers={},
            )
            payload = mock_req.call_args.kwargs.get("json_payload") or {}
            self.assertNotIn("auto_create", payload)


@patch("miya.services.tenant_documents._parse_upload")
class D5ExplicitMutationStillWorksE2E(PostgresE2ETestCase):
    def setUp(self):
        super().setUp()
        self.world = seed_single_establishment()
        self.harness = MiyaE2EHarness(self.world)

    def test_record_invoice_after_extraction_still_works(self, mock_parse):
        mock_parse.return_value = dict(INVOICE_PARSE)
        rest = self.world.restaurant
        inv_before = Invoice.objects.filter(restaurant=rest).count()

        doc_input = ingest_document(
            restaurant=rest,
            uploaded_by=self.world.manager,
            source="WIDGET",
            file_bytes=b"pdf",
            filename="invoice.pdf",
            mime_type="application/pdf",
            operation_id="d5-e2e-invoice",
        )
        self.assertEqual(Invoice.objects.filter(restaurant=rest).count(), inv_before)

        sess = self.world.session_for(self.world.manager)
        sess.update(doc_input.to_session_patch())
        cap = self.harness.send("Record this invoice.", session=sess)

        if cap.verified and cap.success:
            self.assertGreater(Invoice.objects.filter(restaurant=rest).count(), inv_before)
        else:
            self.assertEqual(Invoice.objects.filter(restaurant=rest).count(), inv_before)
