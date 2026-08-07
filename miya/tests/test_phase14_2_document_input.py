"""Phase 14.2 — DocumentInput ingestion: UPLOAD ≠ MUTATION."""
from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase

from accounts.models import BusinessLocation, CustomUser, Restaurant
from finance.models import Invoice
from miya.models import TenantDocument
from miya.services.document_input import DocumentInput, ingest_document
from payroll.models import ComplianceDocument
from staff.models_task import SafetyConcernReport


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
        "invoice_number": "INV-142",
    },
}

INCIDENT_PARSE = {
    "category": "equipment_damage",
    "confidence": 0.88,
    "summary": "Broken freezer in kitchen",
    "fields": {"incident_type": "Maintenance", "severity": "HIGH"},
}


def _seed_world():
    rest = Restaurant.objects.create(
        name="Phase142 Rest",
        email="p142@test.mizan.local",
        timezone="Africa/Casablanca",
    )
    loc = BusinessLocation.objects.create(
        restaurant=rest,
        name="Main",
        is_primary=True,
        is_active=True,
    )
    other_loc = BusinessLocation.objects.create(
        restaurant=rest,
        name="Other Site",
        is_primary=False,
        is_active=True,
    )
    manager = CustomUser.objects.create_user(
        email="mgr-p142@test.mizan.local",
        password="testpass",
        first_name="Mgr",
        last_name="Test",
        role="MANAGER",
        restaurant=rest,
        primary_location=loc,
    )
    manager.managed_locations.add(loc)
    return rest, loc, other_loc, manager


@patch("miya.services.tenant_documents._parse_upload")
class DocumentInputIngestionTests(TestCase):
    def setUp(self):
        self.rest, self.loc, self.other_loc, self.manager = _seed_world()
        self.pdf_bytes = b"%PDF-1.4 fake insurance pdf"
        self.img_bytes = b"\xff\xd8\xff fake jpeg"

    def test_01_upload_pdf_creates_tenant_document(self, mock_parse):
        mock_parse.return_value = dict(INSURANCE_PARSE)
        before = TenantDocument.objects.filter(restaurant=self.rest).count()
        doc_input = ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.pdf_bytes,
            filename="insurance.pdf",
            mime_type="application/pdf",
            caption="Insurance policy",
            location_id=str(self.loc.id),
            channel="dashboard",
        )
        self.assertEqual(TenantDocument.objects.filter(restaurant=self.rest).count(), before + 1)
        self.assertTrue(doc_input.document_id)
        self.assertEqual(doc_input.source, "WIDGET")

    def test_02_upload_image_creates_tenant_document(self, mock_parse):
        mock_parse.return_value = dict(INVOICE_PARSE)
        doc_input = ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WHATSAPP",
            file_bytes=self.img_bytes,
            filename="receipt.jpg",
            mime_type="image/jpeg",
            channel="whatsapp",
        )
        self.assertTrue(doc_input.document_id)
        self.assertEqual(doc_input.channel, "whatsapp")

    def test_03_extraction_succeeds(self, mock_parse):
        mock_parse.return_value = dict(INSURANCE_PARSE)
        doc_input = ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.pdf_bytes,
            filename="insurance.pdf",
            mime_type="application/pdf",
        )
        self.assertEqual(doc_input.extraction_status, "ok")

    def test_04_extraction_returns_normalized_structured_fields(self, mock_parse):
        mock_parse.return_value = dict(INVOICE_PARSE)
        doc_input = ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.img_bytes,
            filename="invoice.jpg",
            mime_type="image/jpeg",
        )
        self.assertEqual(doc_input.structured_fields.get("vendor"), "Acme Foods")
        self.assertEqual(doc_input.structured_fields.get("amount"), "1500.00")

    def test_05_upload_does_not_create_invoice(self, mock_parse):
        mock_parse.return_value = dict(INVOICE_PARSE)
        inv_before = Invoice.objects.filter(restaurant=self.rest).count()
        ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.img_bytes,
            filename="invoice.jpg",
            mime_type="image/jpeg",
        )
        self.assertEqual(Invoice.objects.filter(restaurant=self.rest).count(), inv_before)

    def test_06_upload_does_not_create_compliance(self, mock_parse):
        mock_parse.return_value = dict(INSURANCE_PARSE)
        comp_before = ComplianceDocument.objects.filter(restaurant=self.rest).count()
        ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.pdf_bytes,
            filename="insurance.pdf",
            mime_type="application/pdf",
            caption="insurance renewal",
        )
        self.assertEqual(
            ComplianceDocument.objects.filter(restaurant=self.rest).count(),
            comp_before,
        )

    def test_07_upload_does_not_create_incident(self, mock_parse):
        mock_parse.return_value = dict(INCIDENT_PARSE)
        inc_before = SafetyConcernReport.objects.filter(restaurant=self.rest).count()
        ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.img_bytes,
            filename="damage.jpg",
            mime_type="image/jpeg",
        )
        self.assertEqual(
            SafetyConcernReport.objects.filter(restaurant=self.rest).count(),
            inc_before,
        )

    def test_13_duplicate_retry_does_not_duplicate_document(self, mock_parse):
        mock_parse.return_value = dict(INSURANCE_PARSE)
        op = "op-retry-142"
        first = ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.pdf_bytes,
            filename="insurance.pdf",
            mime_type="application/pdf",
            operation_id=op,
        )
        count_after_first = TenantDocument.objects.filter(restaurant=self.rest).count()
        second = ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.pdf_bytes,
            filename="insurance.pdf",
            mime_type="application/pdf",
            operation_id=op,
        )
        self.assertEqual(
            TenantDocument.objects.filter(restaurant=self.rest).count(),
            count_after_first,
        )
        self.assertEqual(first.document_id, second.document_id)

    def test_14_wrong_establishment_rejected(self, mock_parse):
        mock_parse.return_value = dict(INSURANCE_PARSE)
        staff = CustomUser.objects.create_user(
            email="staff-p142@test.mizan.local",
            password="testpass",
            first_name="Staff",
            last_name="Only",
            role="WAITER",
            restaurant=self.rest,
            primary_location=self.loc,
        )
        staff.allowed_locations.add(self.loc)
        with self.assertRaises(ValueError) as ctx:
            ingest_document(
                restaurant=self.rest,
                uploaded_by=staff,
                source="WIDGET",
                file_bytes=self.pdf_bytes,
                filename="insurance.pdf",
                mime_type="application/pdf",
                location_id=str(self.other_loc.id),
            )
        self.assertEqual(str(ctx.exception), "establishment_forbidden")

    def test_15_wrong_tenant_rejected(self, mock_parse):
        mock_parse.return_value = dict(INSURANCE_PARSE)
        other_rest = Restaurant.objects.create(
            name="Other Tenant",
            email="other-p142@test.mizan.local",
        )
        self.manager.restaurant = other_rest
        self.manager.save(update_fields=["restaurant"])
        with self.assertRaises(ValueError) as ctx:
            ingest_document(
                restaurant=self.rest,
                uploaded_by=self.manager,
                source="WIDGET",
                file_bytes=self.pdf_bytes,
                filename="insurance.pdf",
                mime_type="application/pdf",
            )
        self.assertEqual(str(ctx.exception), "tenant_mismatch")

    def test_16_dashboard_and_whatsapp_equivalent_document_input(self, mock_parse):
        mock_parse.return_value = dict(INSURANCE_PARSE)
        dash = ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.pdf_bytes,
            filename="insurance.pdf",
            mime_type="application/pdf",
            channel="dashboard",
            operation_id="parity-dash",
        )
        wa = ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WHATSAPP",
            file_bytes=self.pdf_bytes,
            filename="insurance.pdf",
            mime_type="application/pdf",
            channel="whatsapp",
            operation_id="parity-wa",
        )
        self.assertIsInstance(dash, DocumentInput)
        self.assertIsInstance(wa, DocumentInput)
        self.assertEqual(dash.structured_fields.get("expiry_date"), wa.structured_fields.get("expiry_date"))
        self.assertNotEqual(dash.channel, wa.channel)

    def test_17_extraction_failure_does_not_create_mutation(self, mock_parse):
        mock_parse.return_value = {"error": "ocr_failed", "category": "", "confidence": 0}
        inv_before = Invoice.objects.filter(restaurant=self.rest).count()
        comp_before = ComplianceDocument.objects.filter(restaurant=self.rest).count()
        doc_input = ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.pdf_bytes,
            filename="bad.pdf",
            mime_type="application/pdf",
        )
        self.assertEqual(doc_input.extraction_status, "failed")
        self.assertEqual(Invoice.objects.filter(restaurant=self.rest).count(), inv_before)
        self.assertEqual(ComplianceDocument.objects.filter(restaurant=self.rest).count(), comp_before)

    @patch("miya.services.tenant_documents._promote_linked_records")
    def test_promotion_gated_off_by_default(self, mock_promote, mock_parse):
        mock_parse.return_value = dict(INVOICE_PARSE)
        ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.img_bytes,
            filename="invoice.jpg",
            mime_type="image/jpeg",
        )
        mock_promote.assert_not_called()

    @patch("miya.services.tenant_documents._promote_linked_records")
    def test_promotion_only_when_explicit_opt_in(self, mock_promote, mock_parse):
        mock_parse.return_value = dict(INVOICE_PARSE)
        ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.img_bytes,
            filename="invoice.jpg",
            mime_type="image/jpeg",
            promote_linked_records=True,
        )
        mock_promote.assert_called_once()


class AutoCreateDefaultTests(TestCase):
    def setUp(self):
        self.rest, self.loc, _, self.manager = _seed_world()
        self.factory = RequestFactory()

    @patch("dashboard.api.photo_router.parse_photo")
    def test_08_parse_photo_default_auto_create_false(self, mock_parse):
        mock_parse.return_value = dict(INVOICE_PARSE)
        from dashboard.api.photo_router import agent_parse_photo

        req = self.factory.post(
            "/api/dashboard/agent/parse-photo/",
            {"restaurant_id": str(self.rest.id)},
        )
        req.user = self.manager
        req.FILES["image"] = SimpleUploadedFile("inv.jpg", b"img", content_type="image/jpeg")
        resp = agent_parse_photo(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Invoice.objects.filter(restaurant=self.rest).count(), 0)

    @patch("dashboard.api.document_router.parse_document")
    @patch("dashboard.api.document_router.looks_like_process_import", return_value=False)
    def test_09_parse_document_default_auto_create_false(self, _looks, mock_parse):
        mock_parse.return_value = dict(INVOICE_PARSE)
        from dashboard.api.document_router import agent_parse_document

        req = self.factory.post(
            "/api/dashboard/agent/parse-document/",
            {"restaurant_id": str(self.rest.id), "import_processes": "false"},
        )
        req.user = self.manager
        req.FILES["document"] = SimpleUploadedFile(
            "inv.pdf", b"%PDF", content_type="application/pdf"
        )
        resp = agent_parse_document(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Invoice.objects.filter(restaurant=self.rest).count(), 0)
        self.assertEqual(ComplianceDocument.objects.filter(restaurant=self.rest).count(), 0)


class ExplicitMutationControlPlaneTests(TestCase):
    def setUp(self):
        self.rest, self.loc, _, self.manager = _seed_world()

    @patch("miya.services.intelligence.planning.multimodal_workflows.execute_structured_action")
    @patch("miya.services.tenant_documents._parse_upload")
    def test_10_explicit_mutation_uses_structured_action(self, mock_parse, mock_exec):
        mock_parse.return_value = dict(INVOICE_PARSE)
        mock_exec.return_value = MagicMock(
            success=True,
            verified=True,
            message_for_user="Invoice recorded.",
            data={"invoice": {"id": "inv-1"}},
            as_tool_response=lambda: {"success": True, "verified": True},
        )
        from miya.services.intelligence.planning.classify import classify_message
        from miya.services.intelligence.planning.multimodal_workflows import run_invoice_from_media
        from miya.services.intelligence.planning.types import ExecutionPlan, PlanAction
        from miya.services.ops.context import OpsContext

        doc_input = ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=b"img",
            filename="invoice.jpg",
            mime_type="image/jpeg",
        )
        ctx = OpsContext(
            user=self.manager,
            restaurant=self.rest,
            restaurant_id=str(self.rest.id),
            user_id=str(self.manager.id),
            role="MANAGER",
            channel="dashboard",
            language="en",
            location_id=str(self.loc.id),
        )
        intent = classify_message("Record this invoice.")
        plan = ExecutionPlan(
            workflow="invoice_from_media",
            action=PlanAction.EXECUTE,
            intent=intent,
            tool_args={
                "document_id": doc_input.document_id,
                "structured": doc_input.structured_fields,
                "vendor": doc_input.structured_fields.get("vendor"),
                "amount": doc_input.structured_fields.get("amount"),
            },
        )
        run_invoice_from_media(ctx, plan)
        mock_exec.assert_called()
        tool_name = mock_exec.call_args[0][0]
        self.assertEqual(tool_name, "record_invoice")

    @patch("miya.services.ops.invoices.record_invoice")
    def test_11_mutation_verified_before_success(self, mock_record):
        from miya.services.intelligence.actions import execute_structured_action
        from miya.services.ops.context import OpsContext
        from miya.services.ops.result import ok

        mock_record.return_value = ok(
            message="Done",
            data={"invoice": {"id": "x"}},
            verified=True,
        )
        ctx = OpsContext(
            user=self.manager,
            restaurant=self.rest,
            restaurant_id=str(self.rest.id),
            user_id=str(self.manager.id),
            role="MANAGER",
            channel="dashboard",
            language="en",
            location_id=str(self.loc.id),
        )
        result = execute_structured_action(
            "record_invoice",
            {"vendor": "Acme", "amount": "100", "document_id": ""},
            ctx=ctx,
            intent="CREATE",
        )
        self.assertTrue(result.verified)

    @patch("miya.services.ops.invoices.record_invoice")
    def test_12_mutation_creates_operational_event(self, mock_record):
        from miya.models import OperationalEvent
        from miya.services.intelligence.actions import execute_structured_action
        from miya.services.ops.context import OpsContext
        from miya.services.ops.result import ok

        inv = Invoice.objects.create(
            restaurant=self.rest,
            vendor_name="Acme",
            amount="100.00",
            due_date="2026-01-01",
            status="SUBMITTED",
            location=self.loc,
            created_by=self.manager,
            invoice_number="INV-99",
        )
        mock_record.return_value = ok(
            message="Invoice recorded.",
            data={"invoice": {"id": str(inv.id)}},
            verified=True,
        )
        before = OperationalEvent.objects.filter(restaurant=self.rest).count()
        ctx = OpsContext(
            user=self.manager,
            restaurant=self.rest,
            restaurant_id=str(self.rest.id),
            user_id=str(self.manager.id),
            role="MANAGER",
            channel="dashboard",
            language="en",
            location_id=str(self.loc.id),
        )
        execute_structured_action(
            "record_invoice",
            {"vendor": "Acme", "amount": "100"},
            ctx=ctx,
            intent="CREATE",
        )
        self.assertGreater(OperationalEvent.objects.filter(restaurant=self.rest).count(), before)

    @patch("miya.services.tenant_documents._parse_upload")
    def test_18_extraction_output_not_implicit_mutation(self, mock_parse):
        mock_parse.return_value = dict(INVOICE_PARSE)
        inv_before = Invoice.objects.filter(restaurant=self.rest).count()
        doc_input = ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=b"pdf",
            filename="inv.pdf",
            mime_type="application/pdf",
        )
        self.assertTrue(doc_input.structured_fields.get("vendor"))
        self.assertEqual(Invoice.objects.filter(restaurant=self.rest).count(), inv_before)

    @patch("miya.services.intelligence.copilot.orchestrator.run_copilot_turn")
    @patch("miya.services.tenant_documents._parse_upload")
    def test_19_assistant_response_does_not_trigger_hidden_mutation(
        self, mock_parse, mock_copilot
    ):
        mock_parse.return_value = dict(INSURANCE_PARSE)
        doc_input = ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=b"pdf",
            filename="insurance.pdf",
            mime_type="application/pdf",
        )
        from miya.services.intelligence.copilot.types import CopilotResult

        mock_copilot.return_value = CopilotResult(
            reply="I extracted expiry 2026-09-30. Tell me if you want a reminder.",
            success=True,
            verified=False,
            handler="presentation_only",
        )
        comp_before = ComplianceDocument.objects.filter(restaurant=self.rest).count()
        from miya.tests.e2e.harness import MiyaE2EHarness
        from miya.tests.e2e.seed import E2EWorld

        world = E2EWorld(
            suffix="p142",
            restaurant_a=self.rest,
            loc_a=self.loc,
            manager_a=self.manager,
        )
        harness = MiyaE2EHarness(world)
        sess = world.session_for(self.manager)
        sess.update(doc_input.to_session_patch())
        cap = harness.send(
            "Insurance document received.",
            session=sess,
        )
        self.assertEqual(
            ComplianceDocument.objects.filter(restaurant=self.rest).count(),
            comp_before,
        )
        self.assertFalse(cap.verified and "reminder created" in (cap.reply or "").lower())


class MediaToolsDefaultTests(TestCase):
    def test_media_tools_does_not_forward_auto_create(self):
        from miya.services.media_tools import dispatch_parse_photo

        with patch("miya.services.tool_dispatch.dispatch_agent_request") as mock_req:
            mock_req.return_value = (200, {"success": True})
            dispatch_parse_photo(
                {"media_url": "https://example.com/photo.jpg", "auto_create": True},
                {"restaurant_id": "r1"},
                headers={},
            )
            self.assertTrue(mock_req.called)
            payload = mock_req.call_args.kwargs.get("json_payload") or {}
            self.assertNotIn("auto_create", payload)
