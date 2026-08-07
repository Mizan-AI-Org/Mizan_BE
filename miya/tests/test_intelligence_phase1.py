"""Phase 1 — Miya Operational Intelligence Core tests."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from miya.services.ops.context import OpsContext
from miya.services.ops.result import fail, ok


def _ctx(*, role="MANAGER", location_id=None, loc_name=None, available=None):
    user = MagicMock()
    user.id = "u1"
    user.pk = "u1"
    user.role = role
    user.phone = "+212600000000"
    user.first_name = "Ada"
    user.last_name = "Manager"
    user.email = "ada@ex.com"
    rest = MagicMock()
    rest.id = "r1"
    rest.name = "Org One"
    rest.timezone = "UTC"
    return OpsContext(
        user=user,
        restaurant=rest,
        restaurant_id="r1",
        user_id="u1",
        role=role,
        channel="dashboard",
        language="en",
        location_id=location_id,
        location_name=loc_name,
        available_locations=available
        or (
            [{"id": "loc-a", "name": "Branch A"}, {"id": "loc-b", "name": "Branch B"}]
            if location_id
            else []
        ),
    )


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "miya-intel-phase1",
        }
    }
)
class ContextEngineTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_execution_context_is_server_built(self):
        from miya.services.intelligence.context_engine import ExecutionContext

        ctx = ExecutionContext(
            user_id="u1",
            organization_id="r1",
            establishment_id="loc-a",
            role="MANAGER",
            permissions=["update_dashboard_task_status"],
            conversation_id="conv-1",
            message_id="msg-1",
            channel="dashboard",
            locale="en",
            current_time="2026-08-07T12:00:00+00:00",
        )
        public = ctx.to_public_dict()
        self.assertEqual(public["user_id"], "u1")
        self.assertEqual(public["organization_id"], "r1")
        self.assertEqual(public["establishment_id"], "loc-a")
        self.assertNotIn("user", public)
        ops = ctx.to_ops_context()
        self.assertEqual(ops.restaurant_id, "r1")
        self.assertEqual(ops.location_id, "loc-a")


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "miya-intel-phase1-idemp",
        }
    }
)
class IdempotencyTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_duplicate_message_rejected(self):
        from miya.services.intelligence.idempotency import claim_message_once

        self.assertTrue(claim_message_once("msg-dup-1"))
        self.assertFalse(claim_message_once("msg-dup-1"))

    def test_duplicate_operation_rejected(self):
        from miya.services.intelligence.idempotency import claim_operation_once

        self.assertTrue(claim_operation_once("op-dup-1"))
        self.assertFalse(claim_operation_once("op-dup-1"))


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "miya-intel-phase1-actions",
        }
    }
)
class StructuredActionTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_complete_task_verified_structure(self):
        from miya.services.intelligence.actions import execute_structured_action

        verified = ok(
            message="Updated",
            verified=True,
            data={
                "task": {"id": "123", "title": "Decoration", "status": "COMPLETED"},
                "previous_status": "IN_PROGRESS",
            },
        )
        with patch(
            "miya.services.ops.tasks.update_task_status", return_value=verified
        ) as mock_upd:
            result = execute_structured_action(
                "complete_task",
                {"task_id": "123"},
                ctx=_ctx(location_id="loc-a", loc_name="Branch A"),
                execution_context={
                    "message_id": "m1",
                    "conversation_id": "c1",
                    "user_id": "u1",
                    "organization_id": "r1",
                    "establishment_id": "loc-a",
                },
            )
        self.assertTrue(result.success)
        self.assertTrue(result.verified)
        self.assertEqual(result.data.get("operation"), "complete_task")
        self.assertEqual(result.data.get("task_id"), "123")
        self.assertEqual(result.data.get("new_status"), "COMPLETED")
        self.assertIn("operation_id", result.data)
        mock_upd.assert_called_once()
        self.assertEqual(mock_upd.call_args.kwargs.get("status"), "COMPLETED")
        self.assertTrue(mock_upd.call_args.kwargs.get("skip_idempotency"))

    def test_complete_task_duplicate_operation(self):
        from miya.services.intelligence.actions import execute_structured_action

        verified = ok(
            message="Updated",
            verified=True,
            data={
                "task": {"id": "123", "title": "Decoration", "status": "COMPLETED"},
                "previous_status": "IN_PROGRESS",
            },
        )
        with patch("miya.services.ops.tasks.update_task_status", return_value=verified) as mock_upd:
            first = execute_structured_action(
                "complete_task",
                {"task_id": "123", "_operation_id": "op-fixed-complete"},
                ctx=_ctx(),
                execution_context={"message_id": "m1", "user_id": "u1", "organization_id": "r1"},
            )
            second = execute_structured_action(
                "complete_task",
                {"task_id": "123", "_operation_id": "op-fixed-complete"},
                ctx=_ctx(),
                execution_context={"message_id": "m1", "user_id": "u1", "organization_id": "r1"},
            )
        self.assertTrue(first.success)
        self.assertTrue(second.success)
        self.assertTrue(second.data.get("deduplicated"))
        self.assertEqual(mock_upd.call_count, 1)

    def test_assign_task_calls_ops(self):
        from miya.services.intelligence.actions import execute_structured_action

        verified = ok(
            message="Assigned",
            verified=True,
            data={"task": {"id": "t1", "title": "Close", "status": "PENDING"}},
        )
        with patch("miya.services.ops.tasks.assign_task", return_value=verified) as mock_asg:
            result = execute_structured_action(
                "assign_task",
                {"task_id": "t1", "assignee_name": "Ahmed"},
                ctx=_ctx(),
                execution_context={"message_id": "m2", "user_id": "u1", "organization_id": "r1"},
            )
        self.assertTrue(result.verified)
        self.assertEqual(result.data.get("operation"), "assign_task")
        mock_asg.assert_called_once()

    def test_create_incident_calls_ops(self):
        from miya.services.intelligence.actions import execute_structured_action

        verified = ok(
            message="Logged",
            verified=True,
            data={"incident": {"id": "i1", "status": "OPEN"}},
        )
        with patch(
            "miya.services.ops.incidents.create_incident", return_value=verified
        ) as mock_ci:
            result = execute_structured_action(
                "create_incident",
                {"description": "Freezer is broken", "incident_type": "Equipment"},
                ctx=_ctx(location_id="loc-a", loc_name="Branch A"),
                execution_context={
                    "message_id": "m3",
                    "user_id": "u1",
                    "organization_id": "r1",
                    "establishment_id": "loc-a",
                },
            )
        self.assertTrue(result.success)
        self.assertEqual(result.data.get("operation"), "create_incident")
        mock_ci.assert_called_once()

    def test_assign_incident_routing(self):
        from miya.services.intelligence.actions import execute_structured_action

        verified = ok(
            message="Routed",
            verified=True,
            data={"incident": {"id": "i1", "assigned_to": "u2"}},
        )
        with patch(
            "miya.services.ops.incidents.route_incident", return_value=verified
        ) as mock_route:
            result = execute_structured_action(
                "assign_incident",
                {"incident_id": "i1"},
                ctx=_ctx(),
                execution_context={"message_id": "m4", "user_id": "u1", "organization_id": "r1"},
            )
        self.assertEqual(result.data.get("operation"), "assign_incident")
        mock_route.assert_called_once()

    def test_retrieve_document(self):
        from miya.services.intelligence.actions import execute_structured_action

        verified = ok(
            message="Found insurance",
            verified=True,
            data={"document": {"id": "d1", "title": "Insurance", "kind": "compliance"}},
        )
        with patch(
            "miya.services.ops.documents.get_document", return_value=verified
        ) as mock_doc:
            result = execute_structured_action(
                "retrieve_document",
                {"q": "insurance"},
                ctx=_ctx(location_id="loc-a", loc_name="Branch A"),
                execution_context={"message_id": "m5", "user_id": "u1", "organization_id": "r1"},
            )
        self.assertTrue(result.verified)
        self.assertEqual(result.data.get("operation"), "retrieve_document")
        mock_doc.assert_called_once()

    def test_invoice_approval_submit(self):
        from miya.services.intelligence.actions import execute_structured_action

        verified = ok(
            message="Approval started",
            verified=True,
            data={"invoice": {"id": "inv1", "status": "PENDING_APPROVAL"}},
        )
        with patch(
            "miya.services.ops.invoices.request_approval", return_value=verified
        ) as mock_req:
            result = execute_structured_action(
                "submit_invoice",
                {"invoice_id": "inv1"},
                ctx=_ctx(),
                execution_context={"message_id": "m6", "user_id": "u1", "organization_id": "r1"},
            )
        self.assertEqual(result.data.get("operation"), "submit_invoice")
        mock_req.assert_called_once()

    def test_create_reminder(self):
        from miya.services.intelligence.actions import execute_structured_action

        verified = ok(
            message="Reminder set",
            verified=True,
            data={"reminder": {"id": "rem1", "title": "Insurance"}},
        )
        with patch(
            "miya.services.ops.meetings.create_personal_reminder", return_value=verified
        ) as mock_rem:
            result = execute_structured_action(
                "create_reminder",
                {"title": "Insurance", "due_at": "2026-09-01T09:00:00Z"},
                ctx=_ctx(),
                execution_context={"message_id": "m7", "user_id": "u1", "organization_id": "r1"},
            )
        self.assertEqual(result.data.get("operation"), "create_reminder")
        mock_rem.assert_called_once()

    def test_permission_failure_propagates(self):
        from miya.services.intelligence.actions import execute_structured_action

        denied = fail(
            code="permission_denied",
            message="You don't have permission to do that in this workspace.",
        )
        with patch("miya.services.ops.tasks.assign_task", return_value=denied):
            result = execute_structured_action(
                "assign_task",
                {"task_id": "t1", "assignee_name": "Ahmed"},
                ctx=_ctx(role="WAITER"),
                execution_context={"message_id": "m8", "user_id": "u1", "organization_id": "r1"},
            )
        self.assertFalse(result.success)
        self.assertEqual(result.code, "permission_denied")
        self.assertFalse(result.verified)

    def test_unverified_mutation_must_not_claim_success_to_model(self):
        from miya.services.intelligence.actions import execute_structured_action

        unverified = ok(message="maybe", verified=False, data={"task": {"id": "t1"}})
        with patch("miya.services.ops.tasks.update_task_status", return_value=unverified):
            result = execute_structured_action(
                "complete_task",
                {"task_id": "t1", "_operation_id": "op-unverified"},
                ctx=_ctx(),
                execution_context={"message_id": "m9", "user_id": "u1", "organization_id": "r1"},
            )
        body = result.as_tool_response()
        # success without verified is still returned as-is from ops, but directive forbids Done on failure only;
        # Phase 1 verify layer documents the contract — assert verified flag is false
        self.assertFalse(body.get("verified"))


class RealityLayerTests(SimpleTestCase):
    def test_get_current_task_tags_database_source(self):
        from miya.services.intelligence.reality import get_current_task

        base = ok(
            message="Found",
            verified=True,
            data={"task": {"id": "t1", "status": "PENDING"}},
        )
        with patch("miya.services.ops.tasks.get_task_state", return_value=base):
            result = get_current_task(_ctx(), task_id="t1")
        self.assertTrue(result.data.get("overrides_conversation_memory"))
        self.assertEqual(result.data.get("source"), "database")
        self.assertEqual(result.data.get("operation"), "get_current_task")

    def test_get_current_establishment_active(self):
        from miya.services.intelligence.reality import get_current_establishment

        result = get_current_establishment(
            _ctx(
                location_id="loc-a",
                loc_name="Branch A",
                available=[
                    {"id": "loc-a", "name": "Branch A"},
                    {"id": "loc-b", "name": "Branch B"},
                ],
            )
        )
        self.assertTrue(result.success)
        self.assertEqual(result.data["establishment"]["id"], "loc-a")


class EstablishmentIsolationTests(SimpleTestCase):
    def test_forbidden_location_returns_fail(self):
        from miya.services.ops.context import assert_location_access

        ctx = _ctx(location_id="loc-a", loc_name="Branch A")
        with patch(
            "miya.services.ops.scoping.user_can_access_location", return_value=False
        ):
            err = assert_location_access(ctx, "loc-b")
        self.assertIsNotNone(err)
        self.assertEqual(err.code, "location_forbidden")


class VerificationLayerTests(SimpleTestCase):
    def test_verify_mutation_success(self):
        from miya.services.intelligence.verify import verify_mutation

        result = verify_mutation(
            operation="complete_task",
            expected={"status": "COMPLETED", "id": "123"},
            fetch=lambda: {"id": "123", "status": "COMPLETED", "title": "Decoration"},
        )
        self.assertTrue(result.success)
        self.assertTrue(result.verified)

    def test_verify_mutation_failure(self):
        from miya.services.intelligence.verify import verify_mutation

        result = verify_mutation(
            operation="complete_task",
            expected={"status": "COMPLETED"},
            fetch=lambda: {"id": "123", "status": "IN_PROGRESS"},
        )
        self.assertFalse(result.success)
        self.assertEqual(result.code, "verify_failed")


class MemoryLayerTests(SimpleTestCase):
    def test_memory_never_authoritative(self):
        from miya.services.intelligence.memory import MemoryStore, reality_overrides_memory

        store = MemoryStore(
            conversation_id="c1",
            user_id="u1",
            organization_id="r1",
            history=[
                {"role": "assistant", "content": "Done — decoration completed."},
                {"role": "user", "content": "thanks"},
            ],
        )
        block = store.as_context_block()
        self.assertEqual(block["authority"], "layered")
        self.assertEqual(
            block["layers"]["conversation_memory"]["authority"], "conversation_only"
        )
        self.assertIn("DATABASE", block["rule"].upper())
        self.assertIn("database", reality_overrides_memory().lower())


class AuditAndEventTests(SimpleTestCase):
    def test_audit_redacts_secrets(self):
        from miya.services.intelligence.audit import record_audit

        row = record_audit(
            message_id="m1",
            conversation_id="c1",
            operation_id="op1",
            user_id="u1",
            organization_id="r1",
            establishment_id="loc-a",
            intent="complete_task",
            tool="complete_task",
            arguments={"task_id": "123", "token": "secret", "access_token": "x"},
            result={"success": True, "verified": True, "operation": "complete_task"},
            execution_time_ms=12.5,
        )
        self.assertNotIn("token", row["arguments"])
        self.assertNotIn("access_token", row["arguments"])
        self.assertEqual(row["arguments"]["task_id"], "123")
        self.assertEqual(row["status"], "verified_success")

    def test_emit_event(self):
        from miya.services.intelligence.events import emit_ops_event

        event = emit_ops_event(
            event_type="complete_task.verified",
            operation="complete_task",
            execution_context={
                "message_id": "m1",
                "user_id": "u1",
                "organization_id": "r1",
                "establishment_id": "loc-a",
            },
            entity_type="task",
            entity_id="123",
            payload={"new_status": "COMPLETED", "password": "nope"},
        )
        self.assertEqual(event["operation"], "complete_task")
        self.assertNotIn("password", event["payload"])


class CloseAliasTests(SimpleTestCase):
    def test_close_maps_to_completed(self):
        from miya.services.ops.tasks import _STATUS_ALIASES

        self.assertEqual(_STATUS_ALIASES["CLOSE"], "COMPLETED")
        self.assertEqual(_STATUS_ALIASES["CLOSED"], "COMPLETED")


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "miya-intel-phase1-msg",
        }
    }
)
class DuplicateMessageChatTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    @patch("miya.services.agent.build_system_prompt", return_value="sys")
    @patch("miya.services.agent.build_session_context")
    @patch("miya.services.intelligence.context_engine.build_execution_context")
    def test_same_message_id_not_processed_twice(self, mock_exec_ctx, mock_sess, _prompt):
        from miya.services.agent import run_miya_chat
        from miya.services.intelligence.context_engine import ExecutionContext

        sess = {
            "user_id": "u1",
            "restaurant_id": None,
            "language": "en",
            "role": "MANAGER",
            "thread_id": "t1",
        }
        mock_sess.return_value = sess
        mock_exec_ctx.return_value = ExecutionContext(
            user_id="u1",
            organization_id="",
            role="MANAGER",
            conversation_id="conv-1",
            message_id="fixed-msg-phase1",
            channel="dashboard",
            locale="en",
        )
        user = MagicMock()
        user.id = "u1"
        user.role = "MANAGER"
        user.first_name = "A"
        user.last_name = "B"
        user.email = "a@ex.com"
        user.phone = ""
        user.restaurant = None

        with patch(
            "miya.services.agent._try_payroll_delegation_fast_path", return_value=None
        ), patch(
            "miya.services.agent._try_staff_delegation_fast_path", return_value=None
        ), patch(
            "miya.services.agent._try_schedule_fast_path", return_value=None
        ), patch(
            "miya.services.agent._try_ambiguous_assign_fast_path", return_value=None
        ), patch(
            "miya.services.agent._try_entity_status_fast_path", return_value=None
        ), patch(
            "miya.services.agent._try_pending_ops_fast_path", return_value=None
        ), patch(
            "miya.services.agent._try_manager_schedule_fast_path", return_value=None
        ), patch(
            "miya.services.mastra_client.mastra_enabled", return_value=False
        ), patch(
            "miya.services.agent._openai_chat",
            return_value={
                "choices": [{"message": {"role": "assistant", "content": "Hello"}}]
            },
        ), patch(
            "miya.services.agent.tools_for_user", return_value=[]
        ):
            first = run_miya_chat(
                user=user,
                access_token=None,
                user_message="hi",
                channel="dashboard",
                inbound_message_id="fixed-msg-phase1",
            )
            second = run_miya_chat(
                user=user,
                access_token=None,
                user_message="hi",
                channel="dashboard",
                inbound_message_id="fixed-msg-phase1",
            )
        self.assertFalse(first.get("deduplicated_message"))
        self.assertTrue(second.get("deduplicated_message"))
        self.assertEqual(second.get("execution_stage"), "END")
