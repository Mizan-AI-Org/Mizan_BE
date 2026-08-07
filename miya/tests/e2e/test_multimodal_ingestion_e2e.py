"""Phase 14.2 — Multimodal ingestion PostgreSQL E2E (UPLOAD ≠ MUTATION)."""
from __future__ import annotations

from unittest.mock import patch

from finance.models import Invoice
from miya.models import OperationalEvent, TenantDocument
from payroll.models import ComplianceDocument
from scheduling.memory_models import PersonalReminder

from miya.services.document_input import ingest_document
from miya.tests.e2e.harness import MiyaE2EHarness, PostgresE2ETestCase
from miya.tests.e2e.seed import count_audit_events, seed_single_establishment


INSURANCE_PARSE = {
    "category": "insurance",
    "confidence": 0.91,
    "summary": "Insurance policy expiring 30 September 2026",
    "fields": {"expiry_date": "2026-09-30", "document_type": "insurance"},
}

INVOICE_PARSE = {
    "category": "invoice_or_receipt",
    "confidence": 0.92,
    "summary": "Invoice from Acme Foods for 1500 MAD",
    "fields": {
        "vendor": "Acme Foods",
        "amount": "1500.00",
        "currency": "MAD",
        "invoice_number": "INV-E2E-142",
    },
}


@patch("miya.services.tenant_documents._parse_upload")
class InsuranceUploadE2ETests(PostgresE2ETestCase):
    """UPLOAD INSURANCE → STORE → EXTRACT → NO AUTO REMINDER → USER REQUESTS → VERIFY."""

    def setUp(self):
        super().setUp()
        self.world = seed_single_establishment()
        self.harness = MiyaE2EHarness(self.world)
        self.pdf_bytes = b"%PDF-1.4 e2e insurance policy"

    def test_e2e_insurance_upload_no_auto_reminder_then_user_requests(
        self, mock_parse
    ):
        mock_parse.return_value = dict(INSURANCE_PARSE)
        rest = self.world.restaurant
        mgr = self.world.manager
        loc = self.world.loc_a

        comp_before = ComplianceDocument.objects.filter(restaurant=rest).count()
        rem_before = PersonalReminder.objects.filter(restaurant=rest).count()
        audit_before = count_audit_events(restaurant_id=rest.id)

        doc_input = ingest_document(
            restaurant=rest,
            uploaded_by=mgr,
            source="WIDGET",
            file_bytes=self.pdf_bytes,
            filename="insurance.pdf",
            mime_type="application/pdf",
            caption="Insurance policy",
            location_id=str(loc.id),
            channel="dashboard",
            operation_id="e2e-insurance-142",
        )

        self.assertTrue(TenantDocument.objects.filter(pk=doc_input.document_id).exists())
        self.assertEqual(doc_input.structured_fields.get("expiry_date"), "2026-09-30")
        self.assertEqual(ComplianceDocument.objects.filter(restaurant=rest).count(), comp_before)
        self.assertEqual(PersonalReminder.objects.filter(restaurant=rest).count(), rem_before)

        sess = self.world.session_for(mgr)
        sess.update(doc_input.to_session_patch())

        cap_understand = self.harness.send(
            "I uploaded an insurance document. What did you extract?",
            session=sess,
        )
        self.assertEqual(PersonalReminder.objects.filter(restaurant=rest).count(), rem_before)
        self.assertFalse(
            cap_understand.verified
            and "reminder created" in (cap_understand.reply or "").lower()
        )

        cap_remind = self.harness.send(
            "Set a reminder for this insurance expiry.",
            session=sess,
        )
        rem_after = PersonalReminder.objects.filter(restaurant=rest).count()
        audit_after = count_audit_events(restaurant_id=rest.id)

        if cap_remind.verified and cap_remind.success:
            self.assertGreater(rem_after, rem_before)
            self.assertGreater(audit_after, audit_before)
            self.assertTrue(
                OperationalEvent.objects.filter(restaurant=rest).exists()
            )
        else:
            self.assertEqual(rem_after, rem_before)


@patch("miya.services.tenant_documents._parse_upload")
class InvoiceUploadE2ETests(PostgresE2ETestCase):
    """UPLOAD INVOICE → EXTRACT → NO AUTO INVOICE → USER RECORDS → VERIFY."""

    def setUp(self):
        super().setUp()
        self.world = seed_single_establishment()
        self.harness = MiyaE2EHarness(self.world)
        self.img_bytes = b"\xff\xd8\xff e2e invoice jpeg"

    def test_e2e_invoice_upload_no_auto_mutation_then_user_records(self, mock_parse):
        mock_parse.return_value = dict(INVOICE_PARSE)
        rest = self.world.restaurant
        mgr = self.world.manager

        inv_before = Invoice.objects.filter(restaurant=rest).count()
        audit_before = count_audit_events(restaurant_id=rest.id, entity_type="invoice")

        doc_input = ingest_document(
            restaurant=rest,
            uploaded_by=mgr,
            source="WIDGET",
            file_bytes=self.img_bytes,
            filename="invoice.jpg",
            mime_type="image/jpeg",
            channel="dashboard",
            operation_id="e2e-invoice-142",
        )

        self.assertTrue(doc_input.structured_fields.get("vendor"))
        self.assertEqual(Invoice.objects.filter(restaurant=rest).count(), inv_before)

        sess = self.world.session_for(mgr)
        sess.update(doc_input.to_session_patch())

        cap = self.harness.send("Record this invoice.", session=sess)
        inv_after = Invoice.objects.filter(restaurant=rest).count()

        if cap.verified and cap.success:
            self.assertGreater(inv_after, inv_before)
            self.assertTrue(cap.verified)
            self.assertGreater(
                count_audit_events(restaurant_id=rest.id),
                audit_before,
            )
        else:
            self.assertEqual(inv_after, inv_before)


@patch("miya.services.tenant_documents._parse_upload")
class WhatsAppParityE2ETests(PostgresE2ETestCase):
    def setUp(self):
        super().setUp()
        self.world = seed_single_establishment()
        self.pdf_bytes = b"%PDF whatsapp parity"

    def test_e2e_whatsapp_ingest_produces_document_input(self, mock_parse):
        mock_parse.return_value = dict(INSURANCE_PARSE)
        doc_input = ingest_document(
            restaurant=self.world.restaurant,
            uploaded_by=self.world.manager,
            source="WHATSAPP",
            file_bytes=self.pdf_bytes,
            filename="policy.pdf",
            mime_type="application/pdf",
            channel="whatsapp",
            operation_id="e2e-wa-parity",
        )
        self.assertEqual(doc_input.channel, "whatsapp")
        self.assertEqual(doc_input.source, "WHATSAPP")
        self.assertFalse(
            ComplianceDocument.objects.filter(restaurant=self.world.restaurant).exists()
        )
