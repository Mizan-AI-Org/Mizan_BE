"""Phase 14.2.1 — import_processes hardening on agent parse_document path."""
from __future__ import annotations

from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase

from accounts.models import BusinessLocation, CustomUser, Restaurant
from miya.models import OperationalEvent
from miya.tests.e2e.harness import MiyaE2EHarness, PostgresE2ETestCase
from miya.tests.e2e.seed import seed_single_establishment
from scheduling.task_templates import TaskTemplate


PROCESS_CSV = (
    "process_name,task_title\n"
    "Runner Opening,Unlock front door\n"
    "Runner Opening,Turn on lights\n"
    "Closing Checklist,Lock doors\n"
).encode("utf-8")

PROCESS_CLASSIFICATION = {
    "category": "process_checklist",
    "confidence": 0.88,
    "summary": "Opening and closing checklists",
    "fields": {},
}


def _seed():
    rest = Restaurant.objects.create(
        name="Import142 Rest",
        email="imp142@test.mizan.local",
        timezone="Africa/Casablanca",
    )
    loc = BusinessLocation.objects.create(
        restaurant=rest, name="Main", is_primary=True, is_active=True
    )
    mgr = CustomUser.objects.create_user(
        email="imp142-mgr@test.mizan.local",
        password="testpass",
        first_name="Mgr",
        last_name="Imp",
        role="MANAGER",
        restaurant=rest,
        primary_location=loc,
    )
    mgr.managed_locations.add(loc)
    return rest, loc, mgr


class ImportProcessesAgentPathTests(TestCase):
    def setUp(self):
        self.rest, self.loc, self.manager = _seed()
        self.factory = RequestFactory()

    @patch("dashboard.api.document_router.parse_document")
    @patch("scheduling.process_template_import_service.bulk_create_task_templates")
    def test_parse_document_import_processes_true_does_not_create_templates(
        self, mock_bulk, mock_classify
    ):
        mock_classify.return_value = dict(PROCESS_CLASSIFICATION)
        from dashboard.api.document_router import agent_parse_document

        before = TaskTemplate.objects.filter(restaurant=self.rest).count()
        req = self.factory.post(
            "/api/dashboard/agent/parse-document/",
            {
                "restaurant_id": str(self.rest.id),
                "import_processes": "true",
                "note": "import processes",
            },
        )
        req.user = self.manager
        req.FILES["document"] = SimpleUploadedFile(
            "processes.csv", PROCESS_CSV, content_type="text/csv"
        )
        resp = agent_parse_document(req)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data.get("success"))
        self.assertIn("process_preview", resp.data)
        self.assertGreater(resp.data["process_preview"]["template_count"], 0)
        mock_bulk.assert_not_called()
        self.assertEqual(TaskTemplate.objects.filter(restaurant=self.rest).count(), before)

    @patch("dashboard.api.document_router.parse_document")
    def test_filename_auto_detect_returns_preview_not_mutation(self, mock_classify):
        mock_classify.return_value = {"category": "other", "confidence": 0.7, "summary": "doc", "fields": {}}
        from dashboard.api.document_router import agent_parse_document

        before = TaskTemplate.objects.filter(restaurant=self.rest).count()
        req = self.factory.post(
            "/api/dashboard/agent/parse-document/",
            {"restaurant_id": str(self.rest.id)},
        )
        req.user = self.manager
        req.FILES["document"] = SimpleUploadedFile(
            "processes.csv", PROCESS_CSV, content_type="text/csv"
        )
        resp = agent_parse_document(req)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("process_preview", resp.data)
        self.assertEqual(TaskTemplate.objects.filter(restaurant=self.rest).count(), before)

    @patch("dashboard.api.document_router.parse_document")
    def test_process_checklist_classification_returns_preview(self, mock_classify):
        mock_classify.return_value = dict(PROCESS_CLASSIFICATION)
        from dashboard.api.document_router import agent_parse_document

        req = self.factory.post(
            "/api/dashboard/agent/parse-document/",
            {"restaurant_id": str(self.rest.id)},
        )
        req.user = self.manager
        req.FILES["document"] = SimpleUploadedFile(
            "doc.pdf", b"plain text fallback", content_type="application/pdf"
        )
        with patch(
            "dashboard.api.document_router.parse_process_templates_from_bytes",
            return_value={
                "templates": [{"name": "Test Process", "tasks": [{"title": "Step 1"}], "template_type": "CUSTOM"}],
                "errors": [],
            },
        ):
            resp = agent_parse_document(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["action_taken"]["type"], "process_import_preview")

    @patch("dashboard.api.document_router.parse_document")
    def test_invoice_extraction_still_succeeds(self, mock_classify):
        mock_classify.return_value = {
            "category": "invoice_or_receipt",
            "confidence": 0.9,
            "summary": "Invoice",
            "fields": {"vendor": "Acme", "amount": "100"},
        }
        from dashboard.api.document_router import agent_parse_document

        req = self.factory.post(
            "/api/dashboard/agent/parse-document/",
            {"restaurant_id": str(self.rest.id), "import_processes": "true"},
        )
        req.user = self.manager
        req.FILES["document"] = SimpleUploadedFile(
            "invoice.pdf", b"%PDF", content_type="application/pdf"
        )
        resp = agent_parse_document(req)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("classification", resp.data)
        self.assertNotIn("process_preview", resp.data)

    def test_media_tools_strips_import_processes(self):
        from miya.services.media_tools import dispatch_parse_document

        with patch("miya.services.tool_dispatch.dispatch_multipart_agent_request") as mock_req:
            mock_req.return_value = (200, {"success": True})
            dispatch_parse_document(
                {"document_base64": "dGVzdA==", "import_processes": True},
                {"restaurant_id": str(self.rest.id)},
                headers={},
            )
            form = mock_req.call_args.kwargs.get("form_data") or {}
            self.assertNotIn("import_processes", form)


class ExplicitImportEndpointTests(TestCase):
    """Dedicated agent_import_process_templates (D) — isolated from parse_document."""

    def setUp(self):
        self.rest, self.loc, self.manager = _seed()
        self.other = Restaurant.objects.create(
            name="Other Rest",
            email="other-imp@test.mizan.local",
        )
        self.factory = RequestFactory()

    @patch("scheduling.views_agent._resolve_restaurant_for_agent")
    def test_explicit_import_endpoint_still_creates_templates(self, mock_resolve):
        mock_resolve.return_value = (self.rest, self.manager, None)
        from scheduling.views_agent import agent_import_process_templates

        before = TaskTemplate.objects.filter(restaurant=self.rest).count()
        req = self.factory.post(
            "/api/scheduling/agent/import-process-templates/",
            {"restaurant_id": str(self.rest.id), "note": "explicit import", "operation_id": "t-explicit-1"},
        )
        req.FILES["document"] = SimpleUploadedFile(
            "processes.csv", PROCESS_CSV, content_type="text/csv"
        )
        resp = agent_import_process_templates(req)
        self.assertIn(resp.status_code, (200, 201))
        self.assertTrue(resp.data.get("verified"))
        self.assertGreater(TaskTemplate.objects.filter(restaurant=self.rest).count(), before)

    @patch("scheduling.views_agent._resolve_restaurant_for_agent")
    def test_duplicate_retry_skips_second_create(self, mock_resolve):
        mock_resolve.return_value = (self.rest, self.manager, None)
        from scheduling.views_agent import agent_import_process_templates

        req = self.factory.post(
            "/api/scheduling/agent/import-process-templates/",
            {"restaurant_id": str(self.rest.id), "operation_id": "t-dup-retry"},
        )
        req.FILES["document"] = SimpleUploadedFile(
            "processes.csv", PROCESS_CSV, content_type="text/csv"
        )
        agent_import_process_templates(req)
        count_after_first = TaskTemplate.objects.filter(restaurant=self.rest).count()
        agent_import_process_templates(req)
        self.assertEqual(TaskTemplate.objects.filter(restaurant=self.rest).count(), count_after_first)

    @patch("scheduling.views_agent._resolve_restaurant_for_agent")
    def test_explicit_import_emits_operational_event(self, mock_resolve):
        mock_resolve.return_value = (self.rest, self.manager, None)
        from scheduling.views_agent import agent_import_process_templates

        before = OperationalEvent.objects.filter(restaurant=self.rest).count()
        req = self.factory.post(
            "/api/scheduling/agent/import-process-templates/",
            {"restaurant_id": str(self.rest.id), "operation_id": "t-audit-1421"},
        )
        req.FILES["document"] = SimpleUploadedFile(
            "processes.csv", PROCESS_CSV, content_type="text/csv"
        )
        agent_import_process_templates(req)
        self.assertGreater(OperationalEvent.objects.filter(restaurant=self.rest).count(), before)

    @patch("scheduling.views_agent._resolve_restaurant_for_agent")
    def test_explicit_import_scoped_to_restaurant(self, mock_resolve):
        mock_resolve.return_value = (self.other, self.manager, None)
        from scheduling.views_agent import agent_import_process_templates

        req = self.factory.post(
            "/api/scheduling/agent/import-process-templates/",
            {"restaurant_id": str(self.other.id)},
        )
        req.FILES["document"] = SimpleUploadedFile(
            "processes.csv", PROCESS_CSV, content_type="text/csv"
        )
        resp = agent_import_process_templates(req)
        self.assertEqual(TaskTemplate.objects.filter(restaurant=self.other).count(), 0)
        self.assertEqual(resp.status_code, 403)


class ImportProcessesE2ETests(PostgresE2ETestCase):
    def setUp(self):
        super().setUp()
        self.world = seed_single_establishment()
        self.harness = MiyaE2EHarness(self.world)

    @patch("dashboard.api.document_router.parse_document")
    def test_e2e_parse_document_no_silent_template_create(self, mock_classify):
        mock_classify.return_value = dict(PROCESS_CLASSIFICATION)
        from dashboard.api.document_router import agent_parse_document

        rest = self.world.restaurant
        before = TaskTemplate.objects.filter(restaurant=rest).count()
        factory = RequestFactory()
        req = factory.post(
            "/api/dashboard/agent/parse-document/",
            {"restaurant_id": str(rest.id), "import_processes": "true"},
        )
        req.user = self.world.manager
        req.FILES["document"] = SimpleUploadedFile(
            "processes.csv", PROCESS_CSV, content_type="text/csv"
        )
        resp = agent_parse_document(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(TaskTemplate.objects.filter(restaurant=rest).count(), before)

    def test_e2e_record_invoice_control_plane_still_works(self):
        from miya.services.document_input import ingest_document

        inv_before = __import__("finance.models", fromlist=["Invoice"]).Invoice.objects.filter(
            restaurant=self.world.restaurant
        ).count()
        with patch("miya.services.tenant_documents._parse_upload") as mock_parse:
            mock_parse.return_value = {
                "category": "invoice_or_receipt",
                "confidence": 0.9,
                "fields": {"vendor": "Acme", "amount": "500"},
            }
            doc_input = ingest_document(
                restaurant=self.world.restaurant,
                uploaded_by=self.world.manager,
                source="WIDGET",
                file_bytes=b"pdf",
                filename="inv.pdf",
                mime_type="application/pdf",
            )
        sess = self.world.session_for(self.world.manager)
        sess.update(doc_input.to_session_patch())
        cap = self.harness.send("Record this invoice.", session=sess)
        inv_after = __import__("finance.models", fromlist=["Invoice"]).Invoice.objects.filter(
            restaurant=self.world.restaurant
        ).count()
        if cap.verified and cap.success:
            self.assertGreater(inv_after, inv_before)
