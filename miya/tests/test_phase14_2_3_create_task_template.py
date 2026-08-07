"""Phase 14.2.3 — D-path agent_create_task_template hardening."""
from __future__ import annotations

import json
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase

from accounts.models import BusinessLocation, CustomUser, Restaurant
from core.operational_audit.service import TASK_TEMPLATE_CREATED
from miya.models import OperationalEvent
from miya.services.ops.context import OpsContext
from miya.services.ops.process_templates import create_task_template
from miya.tests.e2e.harness import PostgresE2ETestCase
from miya.tests.e2e.seed import seed_single_establishment
from scheduling.task_templates import TaskTemplate

CREATE_PAYLOAD = {
    "name": "Runner Opening Checklist",
    "template_type": "OPENING",
    "tasks": [
        {"title": "Unlock front door", "priority": "MEDIUM"},
        {"title": "Turn on lights", "priority": "HIGH"},
    ],
}


def _seed():
    rest = Restaurant.objects.create(
        name="CreateTpl Rest",
        email="createtpl@test.mizan.local",
        timezone="Africa/Casablanca",
    )
    loc = BusinessLocation.objects.create(
        restaurant=rest, name="Main", is_primary=True, is_active=True
    )
    mgr = CustomUser.objects.create_user(
        email="createtpl-mgr@test.mizan.local",
        password="testpass",
        first_name="Mgr",
        last_name="Create",
        role="MANAGER",
        restaurant=rest,
        primary_location=loc,
    )
    mgr.managed_locations.add(loc)
    waiter = CustomUser.objects.create_user(
        email="createtpl-waiter@test.mizan.local",
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
            "user_id": str(user.id) if user else "",
            "role": getattr(user, "role", "") if user else "",
            "location_id": str(loc.id),
            "channel": "agent",
        },
    )


class CreateTaskTemplateDomainServiceTests(TestCase):
    def setUp(self):
        self.rest, self.loc, self.manager, self.waiter = _seed()

    def test_authorized_creation_succeeds_verified(self):
        result = create_task_template(
            _ctx(self.manager, self.rest, self.loc),
            name=CREATE_PAYLOAD["name"],
            tasks=CREATE_PAYLOAD["tasks"],
            template_type=CREATE_PAYLOAD["template_type"],
            operation_id="op-auth-1423",
        )
        self.assertTrue(result.success)
        self.assertTrue(result.verified)
        tpl = result.data.get("task_template") or {}
        self.assertEqual(tpl.get("name"), CREATE_PAYLOAD["name"])
        self.assertEqual(tpl.get("tasks_count"), 2)
        row = TaskTemplate.objects.get(id=tpl["id"], restaurant=self.rest)
        self.assertEqual(row.template_type, "OPENING")

    def test_unauthorized_waiter_rejected(self):
        result = create_task_template(
            _ctx(self.waiter, self.rest, self.loc),
            name=CREATE_PAYLOAD["name"],
            tasks=CREATE_PAYLOAD["tasks"],
            operation_id="op-deny-1423",
        )
        self.assertFalse(result.success)
        self.assertEqual(result.code, "permission_denied")

    def test_missing_actor_rejected(self):
        ctx = OpsContext.from_session(
            user=None,
            restaurant=self.rest,
            session_context={"restaurant_id": str(self.rest.id), "channel": "agent"},
        )
        result = create_task_template(
            ctx,
            name=CREATE_PAYLOAD["name"],
            tasks=CREATE_PAYLOAD["tasks"],
        )
        self.assertFalse(result.success)
        self.assertEqual(result.code, "actor_required")

    def test_tenant_mismatch_via_endpoint(self):
        other = Restaurant.objects.create(name="Other", email="o1423@test.mizan.local")
        with patch("scheduling.views_agent._resolve_restaurant_for_agent") as mock_resolve:
            mock_resolve.return_value = (other, self.manager, None)
            from scheduling.views_agent import agent_create_task_template

            req = RequestFactory().post(
                "/api/scheduling/agent/create-task-template/",
                data=json.dumps(CREATE_PAYLOAD),
                content_type="application/json",
            )
            resp = agent_create_task_template(req)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.data.get("error"), "tenant_mismatch")

    def test_operation_id_retry_no_duplicate_templates_or_audit(self):
        op = "op-idem-1423"
        ctx = _ctx(self.manager, self.rest, self.loc)
        first = create_task_template(
            ctx,
            name=CREATE_PAYLOAD["name"],
            tasks=CREATE_PAYLOAD["tasks"],
            operation_id=op,
        )
        self.assertTrue(first.verified)
        count = TaskTemplate.objects.filter(restaurant=self.rest).count()
        audit_count = OperationalEvent.objects.filter(
            restaurant=self.rest, event_type=TASK_TEMPLATE_CREATED
        ).count()
        second = create_task_template(
            ctx,
            name=CREATE_PAYLOAD["name"],
            tasks=CREATE_PAYLOAD["tasks"],
            operation_id=op,
        )
        self.assertTrue(second.verified)
        self.assertTrue(second.data.get("deduplicated"))
        self.assertEqual(TaskTemplate.objects.filter(restaurant=self.rest).count(), count)
        self.assertEqual(
            OperationalEvent.objects.filter(
                restaurant=self.rest, event_type=TASK_TEMPLATE_CREATED
            ).count(),
            audit_count,
        )

    def test_audit_event_emitted_on_create(self):
        before = OperationalEvent.objects.filter(
            restaurant=self.rest, event_type=TASK_TEMPLATE_CREATED
        ).count()
        result = create_task_template(
            _ctx(self.manager, self.rest, self.loc),
            name="Closing Checklist",
            tasks=[{"title": "Lock doors"}],
            operation_id="op-audit-1423",
        )
        after = OperationalEvent.objects.filter(
            restaurant=self.rest, event_type=TASK_TEMPLATE_CREATED
        ).count()
        self.assertTrue(result.data.get("audit_emitted"))
        self.assertEqual(after - before, 1)

    @patch("miya.services.ops.process_templates._verify_created_templates")
    def test_verification_failure_cannot_return_success(self, mock_verify):
        mock_verify.return_value = (
            [],
            __import__("miya.services.ops.result", fromlist=["fail"]).fail(
                code="verification_failed",
                message="Create verification failed.",
            ),
        )
        result = create_task_template(
            _ctx(self.manager, self.rest, self.loc),
            name=CREATE_PAYLOAD["name"],
            tasks=CREATE_PAYLOAD["tasks"],
            operation_id="op-vfail-1423",
        )
        self.assertFalse(result.success)
        self.assertFalse(result.verified)
        self.assertEqual(result.code, "verification_failed")


class CreateTaskTemplateEndpointTests(TestCase):
    def setUp(self):
        self.rest, self.loc, self.manager, _ = _seed()
        self.factory = RequestFactory()

    def _post_create(self, user, payload=None, **extra):
        body = dict(CREATE_PAYLOAD)
        if payload:
            body.update(payload)
        body.update(extra)
        with patch("scheduling.views_agent._resolve_restaurant_for_agent") as mock_resolve:
            mock_resolve.return_value = (self.rest, user, None)
            from scheduling.views_agent import agent_create_task_template

            req = self.factory.post(
                "/api/scheduling/agent/create-task-template/",
                data=json.dumps(body),
                content_type="application/json",
            )
            return agent_create_task_template(req)

    def test_endpoint_missing_actor_rejected(self):
        resp = self._post_create(None)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.data.get("error"), "actor_required")

    def test_endpoint_authorized_returns_verified(self):
        resp = self._post_create(self.manager, operation_id="ep-ver-1423")
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.data.get("verified"))
        self.assertTrue(resp.data.get("success"))


class MiyaCreateIsolationTests(TestCase):
    def test_tools_registry_has_no_create_task_template(self):
        from miya.services.tools import TOOL_SCHEMAS

        names = {t["function"]["name"] for t in TOOL_SCHEMAS}
        self.assertNotIn("create_task_template", names)

    def test_parse_document_has_no_create_task_template_param(self):
        from miya.services.tools import TOOL_SCHEMAS

        parse_doc = next(
            t for t in TOOL_SCHEMAS if t["function"]["name"] == "parse_document"
        )
        props = parse_doc["function"]["parameters"]["properties"]
        self.assertNotIn("create_task_template", props)
        self.assertNotIn("auto_create", props)

    @patch("dashboard.api.document_router.parse_document")
    def test_parse_document_does_not_create_templates(self, mock_classify):
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
            {"restaurant_id": str(rest.id)},
        )
        req.FILES["document"] = SimpleUploadedFile(
            "processes.csv",
            b"process_name,task_title\nOpening,Unlock\n",
            content_type="text/csv",
        )
        resp = agent_parse_document(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(TaskTemplate.objects.filter(restaurant=rest).count(), before)


class CreateTaskTemplatePostgresE2ETests(PostgresE2ETestCase):
    def setUp(self):
        super().setUp()
        self.world = seed_single_establishment()

    @patch("scheduling.views_agent._resolve_restaurant_for_agent")
    def test_e2e_explicit_create_verified_with_audit(self, mock_resolve):
        mock_resolve.return_value = (self.world.restaurant, self.world.manager, None)
        from scheduling.views_agent import agent_create_task_template

        before = OperationalEvent.objects.filter(
            restaurant=self.world.restaurant,
            event_type=TASK_TEMPLATE_CREATED,
        ).count()
        req = RequestFactory().post(
            "/api/scheduling/agent/create-task-template/",
            data=json.dumps({**CREATE_PAYLOAD, "operation_id": "e2e-create-1423"}),
            content_type="application/json",
        )
        resp = agent_create_task_template(req)
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.data.get("verified"))
        self.assertGreater(
            OperationalEvent.objects.filter(
                restaurant=self.world.restaurant,
                event_type=TASK_TEMPLATE_CREATED,
            ).count(),
            before,
        )
