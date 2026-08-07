"""Phase 14.2.2 — D-path agent_import_process_templates hardening."""
from __future__ import annotations

from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase

from accounts.models import BusinessLocation, CustomUser, Restaurant
from core.operational_audit.service import TASK_TEMPLATE_IMPORTED
from miya.models import OperationalEvent
from miya.services.ops.context import OpsContext
from miya.services.ops.process_templates import import_process_templates
from miya.tests.e2e.harness import PostgresE2ETestCase
from miya.tests.e2e.seed import seed_single_establishment
from scheduling.task_templates import TaskTemplate


PROCESS_CSV = (
    "process_name,task_title\n"
    "Runner Opening,Unlock front door\n"
    "Runner Opening,Turn on lights\n"
    "Closing Checklist,Lock doors\n"
).encode("utf-8")

TEMPLATES = [
    {
        "name": "Runner Opening",
        "template_type": "OPENING",
        "tasks": [{"title": "Unlock front door", "priority": "MEDIUM"}],
    },
    {
        "name": "Closing Checklist",
        "template_type": "CLOSING",
        "tasks": [{"title": "Lock doors", "priority": "MEDIUM"}],
    },
]


def _seed():
    rest = Restaurant.objects.create(
        name="DPath Rest",
        email="dpath@test.mizan.local",
        timezone="Africa/Casablanca",
    )
    loc = BusinessLocation.objects.create(
        restaurant=rest, name="Main", is_primary=True, is_active=True
    )
    mgr = CustomUser.objects.create_user(
        email="dpath-mgr@test.mizan.local",
        password="testpass",
        first_name="Mgr",
        last_name="DPath",
        role="MANAGER",
        restaurant=rest,
        primary_location=loc,
    )
    mgr.managed_locations.add(loc)
    waiter = CustomUser.objects.create_user(
        email="dpath-waiter@test.mizan.local",
        password="testpass",
        first_name="Wait",
        last_name="Er",
        role="WAITER",
        restaurant=rest,
        primary_location=loc,
    )
    return rest, loc, mgr, waiter


def _ctx(user, rest, loc):
    return OpsContext.from_session(
        user=user,
        restaurant=rest,
        session_context={
            "restaurant_id": str(rest.id),
            "user_id": str(user.id),
            "role": user.role,
            "location_id": str(loc.id),
            "channel": "agent",
        },
    )


class ProcessTemplateDomainServiceTests(TestCase):
    def setUp(self):
        self.rest, self.loc, self.manager, self.waiter = _seed()

    def test_authorized_import_succeeds_verified(self):
        result = import_process_templates(
            _ctx(self.manager, self.rest, self.loc),
            templates=TEMPLATES,
            operation_id="op-auth-1422",
        )
        self.assertTrue(result.success)
        self.assertTrue(result.verified)
        self.assertEqual(len(result.data.get("created") or []), 2)

    def test_unauthorized_waiter_rejected(self):
        result = import_process_templates(
            _ctx(self.waiter, self.rest, self.loc),
            templates=TEMPLATES,
            operation_id="op-deny-1422",
        )
        self.assertFalse(result.success)
        self.assertEqual(result.code, "permission_denied")

    def test_missing_actor_rejected(self):
        ctx = OpsContext.from_session(
            user=None,
            restaurant=self.rest,
            session_context={"restaurant_id": str(self.rest.id), "channel": "agent"},
        )
        result = import_process_templates(ctx, templates=TEMPLATES)
        self.assertFalse(result.success)
        self.assertEqual(result.code, "actor_required")

    def test_tenant_mismatch_via_endpoint(self):
        other = Restaurant.objects.create(name="Other", email="o@test.mizan.local")
        with patch("scheduling.views_agent._resolve_restaurant_for_agent") as mock_resolve:
            mock_resolve.return_value = (other, self.manager, None)
            from scheduling.views_agent import agent_import_process_templates

            req = RequestFactory().post(
                "/api/scheduling/agent/import-process-templates/",
                {"restaurant_id": str(other.id)},
            )
            req.FILES["document"] = SimpleUploadedFile(
                "processes.csv", PROCESS_CSV, content_type="text/csv"
            )
            resp = agent_import_process_templates(req)
        self.assertEqual(resp.status_code, 403)

    def test_duplicate_names_skipped(self):
        import_process_templates(_ctx(self.manager, self.rest, self.loc), templates=TEMPLATES, operation_id="dup-a")
        result = import_process_templates(
            _ctx(self.manager, self.rest, self.loc),
            templates=TEMPLATES,
            operation_id="dup-b",
        )
        self.assertTrue(result.verified)
        self.assertEqual(len(result.data.get("created") or []), 0)
        self.assertGreater(len(result.data.get("skipped") or []), 0)

    def test_operation_id_retry_no_duplicate_templates_or_audit(self):
        op = "op-idem-1422"
        ctx = _ctx(self.manager, self.rest, self.loc)
        first = import_process_templates(ctx, templates=TEMPLATES, operation_id=op)
        self.assertTrue(first.verified)
        count = TaskTemplate.objects.filter(restaurant=self.rest).count()
        audit_count = OperationalEvent.objects.filter(
            restaurant=self.rest, event_type=TASK_TEMPLATE_IMPORTED
        ).count()
        second = import_process_templates(ctx, templates=TEMPLATES, operation_id=op)
        self.assertTrue(second.verified)
        self.assertTrue(second.data.get("deduplicated"))
        self.assertEqual(TaskTemplate.objects.filter(restaurant=self.rest).count(), count)
        self.assertEqual(
            OperationalEvent.objects.filter(
                restaurant=self.rest, event_type=TASK_TEMPLATE_IMPORTED
            ).count(),
            audit_count,
        )

    def test_audit_event_emitted_per_created_template(self):
        before = OperationalEvent.objects.filter(
            restaurant=self.rest, event_type=TASK_TEMPLATE_IMPORTED
        ).count()
        result = import_process_templates(
            _ctx(self.manager, self.rest, self.loc),
            templates=TEMPLATES,
            operation_id="op-audit-1422",
        )
        after = OperationalEvent.objects.filter(
            restaurant=self.rest, event_type=TASK_TEMPLATE_IMPORTED
        ).count()
        self.assertTrue(result.data.get("audit_emitted"))
        self.assertEqual(after - before, len(result.data.get("created") or []))

    @patch("miya.services.ops.process_templates._verify_created_templates")
    def test_verification_failure_cannot_return_success(self, mock_verify):
        mock_verify.return_value = (
            [],
            __import__("miya.services.ops.result", fromlist=["fail"]).fail(
                code="verification_failed",
                message="Import verification failed.",
            ),
        )
        result = import_process_templates(
            _ctx(self.manager, self.rest, self.loc),
            templates=TEMPLATES,
            operation_id="op-vfail-1422",
        )
        self.assertFalse(result.success)
        self.assertFalse(result.verified)
        self.assertEqual(result.code, "verification_failed")


class DPathEndpointTests(TestCase):
    def setUp(self):
        self.rest, self.loc, self.manager, _ = _seed()
        self.factory = RequestFactory()

    def _post_import(self, user, **extra):
        with patch("scheduling.views_agent._resolve_restaurant_for_agent") as mock_resolve:
            mock_resolve.return_value = (self.rest, user, None)
            from scheduling.views_agent import agent_import_process_templates

            req = self.factory.post(
                "/api/scheduling/agent/import-process-templates/",
                {"restaurant_id": str(self.rest.id), **extra},
            )
            req.FILES["document"] = SimpleUploadedFile(
                "processes.csv", PROCESS_CSV, content_type="text/csv"
            )
            return agent_import_process_templates(req)

    def test_endpoint_missing_actor_rejected(self):
        resp = self._post_import(None)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.data.get("error"), "actor_required")

    def test_endpoint_authorized_returns_verified(self):
        resp = self._post_import(self.manager, operation_id="ep-ver-1422")
        self.assertIn(resp.status_code, (200, 201))
        self.assertTrue(resp.data.get("verified"))


class MiyaIsolationTests(TestCase):
    def test_parse_document_tool_has_no_import_processes_param(self):
        from miya.services.tools import TOOL_SCHEMAS

        parse_doc = next(
            t for t in TOOL_SCHEMAS if t["function"]["name"] == "parse_document"
        )
        props = parse_doc["function"]["parameters"]["properties"]
        self.assertNotIn("import_processes", props)

    def test_tools_registry_has_no_import_process_endpoint(self):
        from miya.services.tools import TOOL_SCHEMAS

        names = {t["function"]["name"] for t in TOOL_SCHEMAS}
        self.assertNotIn("import_process_templates", names)

    @patch("dashboard.api.document_router.parse_document")
    def test_import_processes_true_still_preview_only(self, mock_classify):
        rest, loc, mgr, _ = _seed()
        mock_classify.return_value = {
            "category": "process_checklist",
            "confidence": 0.9,
            "summary": "checklists",
            "fields": {},
        }
        from dashboard.api.document_router import agent_parse_document

        before = TaskTemplate.objects.filter(restaurant=rest).count()
        req = RequestFactory().post(
            "/api/dashboard/agent/parse-document/",
            {"restaurant_id": str(rest.id), "import_processes": "true"},
        )
        req.FILES["document"] = SimpleUploadedFile(
            "processes.csv", PROCESS_CSV, content_type="text/csv"
        )
        resp = agent_parse_document(req)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("process_preview", resp.data)
        self.assertEqual(TaskTemplate.objects.filter(restaurant=rest).count(), before)


class DPathPostgresE2ETests(PostgresE2ETestCase):
    def setUp(self):
        super().setUp()
        self.world = seed_single_establishment()

    @patch("scheduling.views_agent._resolve_restaurant_for_agent")
    def test_e2e_explicit_import_verified_with_audit(self, mock_resolve):
        mock_resolve.return_value = (self.world.restaurant, self.world.manager, None)
        from scheduling.views_agent import agent_import_process_templates

        before = OperationalEvent.objects.filter(
            restaurant=self.world.restaurant,
            event_type=TASK_TEMPLATE_IMPORTED,
        ).count()
        req = RequestFactory().post(
            "/api/scheduling/agent/import-process-templates/",
            {
                "restaurant_id": str(self.world.restaurant.id),
                "operation_id": "e2e-dpath-1422",
            },
        )
        req.FILES["document"] = SimpleUploadedFile(
            "processes.csv", PROCESS_CSV, content_type="text/csv"
        )
        resp = agent_import_process_templates(req)
        self.assertIn(resp.status_code, (200, 201))
        self.assertTrue(resp.data.get("verified"))
        self.assertGreater(
            OperationalEvent.objects.filter(
                restaurant=self.world.restaurant,
                event_type=TASK_TEMPLATE_IMPORTED,
            ).count(),
            before,
        )
