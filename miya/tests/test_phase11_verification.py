"""Phase 11 — verification unification & E2E truth regression tests."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from miya.services.intelligence.mutation_pipeline import (
    enforce_mutation_tool_response,
    finalize_legacy_tool_response,
)
from miya.services.intelligence.mutation_registry import (
    inventory_mutations,
    is_legacy_http_mutation,
    is_mutation_tool,
)
from miya.services.ops.result import ok


class MutationInventoryTests(SimpleTestCase):
    def test_inventory_has_three_buckets(self):
        inv = inventory_mutations()
        self.assertIn("structured_spine", inv)
        self.assertIn("canonical_dispatch", inv)
        self.assertIn("legacy_http_blocked", inv)
        self.assertIn("create_dashboard_task", inv["structured_spine"])
        self.assertIn("update_dashboard_task_status", inv["structured_spine"])

    def test_update_dashboard_task_is_canonical_not_legacy(self):
        self.assertFalse(is_legacy_http_mutation("update_dashboard_task"))
        self.assertTrue(is_mutation_tool("update_dashboard_task"))

    def test_staff_clock_in_is_canonical(self):
        self.assertFalse(is_legacy_http_mutation("staff_clock_in"))
        self.assertTrue(is_mutation_tool("staff_clock_in"))


class MutationGateTests(SimpleTestCase):
    def test_deferred_parse_photo_not_legacy_blocked(self):
        body = finalize_legacy_tool_response(
            "parse_photo",
            status_code=200,
            body={"success": True, "message_for_user": "parsed"},
        )
        # Deferred Phase 14 — still legacy HTTP, not yet canonical verify
        self.assertTrue(body.get("success"))

    def test_legacy_read_post_still_succeeds(self):
        body = finalize_legacy_tool_response(
            "list_dashboard_tasks",
            status_code=200,
            body={"tasks": [], "count": 0},
        )
        self.assertTrue(body["success"])

    def test_unverified_structured_mutation_downgraded(self):
        payload = ok(message="Assigned.", verified=False, data={"task": {"id": "1"}}).as_tool_response()
        payload["success"] = True
        payload["verified"] = False
        gated = enforce_mutation_tool_response("update_dashboard_task_status", payload)
        self.assertFalse(gated["success"])
        self.assertEqual(gated["code"], "unverified")

    def test_verified_structured_mutation_passes(self):
        payload = ok(message="Assigned.", verified=True, data={"task": {"id": "1"}}).as_tool_response()
        gated = enforce_mutation_tool_response("update_dashboard_task_status", payload)
        self.assertTrue(gated["success"])
        self.assertTrue(gated["verified"])


class RequireVerifiedInActionsTests(SimpleTestCase):
    def _ctx(self):
        from miya.services.ops.context import OpsContext

        user = MagicMock()
        user.id = "u1"
        user.pk = "u1"
        user.role = "MANAGER"
        rest = MagicMock()
        rest.id = "r1"
        return OpsContext(
            user=user,
            restaurant=rest,
            restaurant_id="r1",
            user_id="u1",
            role="MANAGER",
            channel="dashboard",
            language="en",
        )

    @patch("miya.services.intelligence.actions._finish")
    @patch("miya.services.intelligence.actions.claim_operation_once", return_value=True)
    @patch("miya.services.intelligence.actions._HANDLERS")
    def test_execute_structured_action_downgrades_unverified_mutation(
        self, mock_handlers, *_mocks
    ):
        from miya.services.intelligence.actions import execute_structured_action

        mock_handlers.get.return_value = lambda ctx, args: ok(
            message="Done?", verified=False, data={"task": {"id": "t1"}}
        )
        result = execute_structured_action(
            "update_dashboard_task_status",
            {"status": "COMPLETED", "q": "closing"},
            ctx=self._ctx(),
            execution_context={"message_id": "m1"},
        )
        self.assertFalse(result.success)
        self.assertEqual(result.code, "unverified")


class StaffTaskStateTests(SimpleTestCase):
    def _staff_ctx(self):
        from miya.services.ops.context import OpsContext

        user = MagicMock()
        user.id = "s1"
        user.pk = "s1"
        user.role = "STAFF"
        rest = MagicMock()
        rest.id = "r1"
        return OpsContext(
            user=user,
            restaurant=rest,
            restaurant_id="r1",
            user_id="s1",
            role="STAFF",
            channel="whatsapp",
            language="en",
        )

    @patch("miya.services.ops.tasks.require_restaurant", return_value=None)
    @patch("miya.services.ops.tasks._resolve_task")
    @patch("miya.services.ops.tasks.user_can_read_task", return_value=True)
    def test_staff_can_read_own_task_state(self, *_mocks):
        from miya.services.ops.tasks import get_task_state

        task = MagicMock()
        task.id = "t1"
        task.title = "Closing photos"
        task.status = "IN_PROGRESS"
        task.assigned_to = None
        task.due_date = None
        task.updated_at = None
        task.priority = "MEDIUM"
        task.category = ""
        _mocks[1].return_value = (task, None)

        result = get_task_state(self._staff_ctx(), q="Closing photos")
        self.assertTrue(result.success)
        self.assertTrue(result.verified)

    @patch("miya.services.ops.tasks.require_restaurant", return_value=None)
    @patch("miya.services.ops.tasks._resolve_task")
    @patch("miya.services.ops.tasks.user_can_read_task", return_value=False)
    def test_staff_denied_other_task_state(self, mock_read, mock_resolve, mock_rest):
        from miya.services.ops.tasks import get_task_state

        task = MagicMock()
        task.id = "t2"
        task.title = "Maxime photos"
        mock_resolve.return_value = (task, None)

        result = get_task_state(self._staff_ctx(), q="Maxime photos")
        self.assertFalse(result.success)
        self.assertEqual(result.code, "permission_denied")

    @patch("accounts.rbac_enforce.user_can_action", return_value=False)
    @patch("accounts.rbac_enforce.miya_has_full_tenant_access", return_value=False)
    @patch("miya.services.ops.tasks.get_task_state")
    def test_get_current_task_auto_mine_only_for_staff(self, mock_state, *_rbac):
        from miya.services.intelligence.reality import get_current_task

        mock_state.return_value = ok(message="ok", verified=True, data={"task": {"id": "1"}})
        get_current_task(self._staff_ctx(), q="my checklist")
        mock_state.assert_called_once()
        self.assertTrue(mock_state.call_args.kwargs.get("mine_only"))


class CanonicalUpdateTaskDispatchTests(SimpleTestCase):
    def _ctx(self):
        from miya.services.ops.context import OpsContext

        user = MagicMock()
        user.id = "u1"
        user.role = "MANAGER"
        rest = MagicMock()
        rest.id = "r1"
        return OpsContext(
            user=user,
            restaurant=rest,
            restaurant_id="r1",
            user_id="u1",
            role="MANAGER",
            channel="dashboard",
            language="en",
        )

    @patch("miya.services.ops.tasks.update_task")
    def test_dispatch_update_dashboard_task(self, mock_update):
        from miya.services.ops import dispatch_canonical_tool

        mock_update.return_value = ok(message="updated", verified=True, data={"task": {"id": "1"}})
        result = dispatch_canonical_tool(
            "update_dashboard_task",
            {"q": "Closing", "priority": "HIGH"},
            ctx=self._ctx(),
        )
        self.assertIsNotNone(result)
        self.assertTrue(result.success)
        self.assertTrue(result.verified)
        mock_update.assert_called_once()
