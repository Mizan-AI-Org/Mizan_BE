"""Phase 2: operational understanding, canonical tools, no false Done."""
from __future__ import annotations

from django.test import SimpleTestCase
from unittest.mock import MagicMock, patch

from miya.services.ops.intent import (
    extract_status_query_subject,
    looks_like_board_briefing,
    looks_like_entity_status,
    looks_like_pronoun_assign,
)
from miya.services.ops.result import fail, ok
from miya.services.agent import _looks_like_pending_ops_query


class OpsIntentTests(SimpleTestCase):
    def test_board_briefing_vs_entity_status(self):
        self.assertTrue(looks_like_board_briefing("what are the pending tasks for today", "MANAGER"))
        self.assertTrue(looks_like_board_briefing("where are we at today?", "OWNER"))
        self.assertFalse(looks_like_board_briefing("Is Ahmed's task completed?", "MANAGER"))
        self.assertFalse(looks_like_board_briefing("what's the status of closing checklist", "MANAGER"))
        self.assertTrue(looks_like_entity_status("Is Ahmed's task completed?"))
        self.assertFalse(looks_like_entity_status("Change Ahmed's task to completed."))
        self.assertTrue(looks_like_entity_status("status of closing checklist"))

    def test_pending_ops_fast_path_does_not_steal_entity_status(self):
        self.assertFalse(_looks_like_pending_ops_query("Is Ahmed's task completed?", "MANAGER"))
        self.assertFalse(_looks_like_pending_ops_query("what's the status of the closing checklist", "MANAGER"))
        self.assertTrue(_looks_like_pending_ops_query("pending tasks for today", "MANAGER"))

    def test_extract_status_subject(self):
        self.assertIn("Ahmed", extract_status_query_subject("Is Ahmed's task completed?"))
        self.assertTrue(extract_status_query_subject("status of closing checklist"))

    def test_pronoun_assign_detection(self):
        self.assertTrue(looks_like_pronoun_assign("Assign it to Ahmed."))
        self.assertTrue(looks_like_pronoun_assign("assign that to Sara"))
        self.assertFalse(looks_like_pronoun_assign("Assign closing checklist to Ahmed."))
        self.assertFalse(looks_like_pronoun_assign("Who works in the kitchen?"))


class OpsResultContractTests(SimpleTestCase):
    def test_failure_forbids_false_done(self):
        body = fail(
            code="assignee_not_found",
            message="I couldn't assign the task to Ahmed because I couldn't find that staff member.",
        ).as_tool_response()
        self.assertFalse(body["success"])
        self.assertFalse(body["verified"])
        self.assertIn("Do NOT tell the user the action succeeded", body["miya_directive"])
        self.assertIn("couldn't assign", body["message_for_user"].lower())

    def test_success_requires_verified_flag(self):
        body = ok(message="Assigned.", verified=True, data={"task": {"id": "1"}}).as_tool_response()
        self.assertTrue(body["success"])
        self.assertTrue(body["verified"])

    def test_clarify_is_not_success(self):
        from miya.services.ops.result import clarify

        body = clarify(message="Which task should I assign?").as_tool_response()
        self.assertFalse(body["success"])
        self.assertTrue(body["needs_clarification"])


class CanonicalDispatchTests(SimpleTestCase):
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

    @patch("miya.services.ops.tasks.assign_task")
    def test_assign_without_task_asks_clarification(self, mock_assign):
        from miya.services.ops import dispatch_canonical_tool

        result = dispatch_canonical_tool(
            "reassign_dashboard_task",
            {"assignee_name": "Ahmed", "task_id": "it"},
            ctx=self._ctx(),
        )
        self.assertIsNotNone(result)
        self.assertFalse(result.success)
        self.assertTrue(result.needs_clarification)
        mock_assign.assert_not_called()

    @patch("miya.services.ops.tasks.assign_task")
    def test_ambiguous_candidates_ask_clarification(self, mock_assign):
        from miya.services.ops import dispatch_canonical_tool

        result = dispatch_canonical_tool(
            "assign_ops_task",
            {
                "assignee_name": "Ahmed",
                "task_id": "",
                "_ambiguous_task_candidates": ["t1", "t2"],
            },
            ctx=self._ctx(),
        )
        self.assertFalse(result.success)
        self.assertTrue(result.needs_clarification)
        mock_assign.assert_not_called()

    @patch("miya.services.ops.staff.find_staff")
    def test_find_staff_kitchen(self, mock_find):
        from miya.services.ops import dispatch_canonical_tool

        mock_find.return_value = ok(
            message="Found 2 staff in kitchen.",
            verified=True,
            data={"staff": [{"name": "Ahmed"}, {"name": "Sara"}], "count": 2},
        )
        result = dispatch_canonical_tool(
            "find_staff",
            {"q": "kitchen"},
            ctx=self._ctx(),
        )
        self.assertTrue(result.success)
        mock_find.assert_called_once()
        kwargs = mock_find.call_args.kwargs
        self.assertEqual(kwargs.get("q") or kwargs.get("name") or "", "kitchen")

    @patch("miya.services.ops.categories.find_category_owners")
    def test_who_is_responsible_for_finance(self, mock_owners):
        from miya.services.ops import dispatch_canonical_tool

        mock_owners.return_value = ok(
            message="FINANCE is owned by: Fatima.",
            verified=True,
            data={"category": "FINANCE", "owners": [{"name": "Fatima"}]},
        )
        result = dispatch_canonical_tool(
            "find_responsible_people",
            {"category": "finance"},
            ctx=self._ctx(),
        )
        self.assertTrue(result.success)
        self.assertIn("Fatima", result.message_for_user)

    @patch("miya.services.ops.incidents.find_incidents")
    def test_todays_incidents(self, mock_inc):
        from miya.services.ops import dispatch_canonical_tool

        mock_inc.return_value = ok(
            message="Found 1 incident(s).",
            verified=True,
            data={"incidents": [{"title": "Spill"}], "count": 1},
        )
        result = dispatch_canonical_tool(
            "find_incidents",
            {"since": "today"},
            ctx=self._ctx(),
        )
        self.assertTrue(result.success)
        mock_inc.assert_called_once()
        self.assertEqual(mock_inc.call_args.kwargs.get("since"), "today")

    @patch("miya.services.ops.incidents.find_incident_responsible")
    def test_who_should_receive_incident(self, mock_resp):
        from miya.services.ops import dispatch_canonical_tool

        mock_resp.return_value = ok(
            message="For 'Safety' incidents, responsible: Omar.",
            verified=True,
            data={"owners": [{"name": "Omar"}]},
        )
        result = dispatch_canonical_tool(
            "find_responsible_people",
            {"category": "Safety incident", "kind": "incident"},
            ctx=self._ctx(),
        )
        self.assertTrue(result.success)
        mock_resp.assert_called_once()


class ConversationExampleRoutingTests(SimpleTestCase):
    """Map conversation examples to expected tool intent (no DB)."""

    EXAMPLES = [
        ("Assign closing checklist to Ahmed.", "assign_or_create"),
        ("Who is responsible for finance?", "find_category_owners"),
        ("Who works in the kitchen?", "find_staff"),
        ("Is Ahmed's task completed?", "entity_status"),
        ("Change Ahmed's task to completed.", "update_status"),
        ("Show me today's incidents.", "find_incidents"),
        ("What happened to the incident from yesterday?", "find_incidents"),
        ("Who should receive this incident?", "find_responsible"),
        ("Assign it to Ahmed.", "clarify_or_assign"),
    ]

    def test_example_intents(self):
        for text, kind in self.EXAMPLES:
            if kind == "entity_status":
                self.assertTrue(looks_like_entity_status(text), text)
                self.assertFalse(looks_like_board_briefing(text, "MANAGER"), text)
            elif kind == "clarify_or_assign":
                self.assertTrue(looks_like_pronoun_assign(text), text)
            elif kind == "find_staff":
                self.assertIn("kitchen", text.lower())
            elif kind == "find_category_owners":
                self.assertIn("finance", text.lower())
            elif kind == "find_incidents":
                self.assertTrue("incident" in text.lower())
            elif kind == "find_responsible":
                self.assertIn("incident", text.lower())
            elif kind == "assign_or_create":
                self.assertFalse(looks_like_pronoun_assign(text), text)
            elif kind == "update_status":
                from miya.services.ops.intent import looks_like_status_write

                self.assertTrue(looks_like_status_write(text), text)
                self.assertFalse(looks_like_entity_status(text), text)


class AmbiguousAssignFastPathTests(SimpleTestCase):
    def test_ambiguous_it_asks_clarification(self):
        from miya.services.agent import _try_ambiguous_assign_fast_path

        user = MagicMock()
        user.id = "u1"
        user.role = "MANAGER"
        session = {"role": "MANAGER", "restaurant_id": "r1", "user_id": "u1", "language": "en"}

        with patch("miya.services.working_set.resolve_ids", return_value=[]), patch(
            "miya.services.working_set.get_entities", return_value=[]
        ):
            out = _try_ambiguous_assign_fast_path(
                user_message="Assign it to Ahmed.",
                session_context=session,
                user=user,
            )
        self.assertIsNotNone(out)
        self.assertIn("which task", out["reply"].lower())
        self.assertNotIn("i assigned", out["reply"].lower())

    def test_named_assign_is_not_hijacked(self):
        from miya.services.agent import _try_ambiguous_assign_fast_path

        user = MagicMock()
        user.role = "MANAGER"
        out = _try_ambiguous_assign_fast_path(
            user_message="Assign closing checklist to Ahmed.",
            session_context={"role": "MANAGER", "language": "en"},
            user=user,
        )
        self.assertIsNone(out)


class ToolSchemaCoverageTests(SimpleTestCase):
    def test_canonical_tools_have_schemas_and_rbac(self):
        from accounts.rbac_enforce import TOOL_REQUIRED_ACTIONS
        from miya.services.ops import CANONICAL_TOOL_NAMES
        from miya.services.tools import TOOL_SCHEMAS

        schema_names = {(s.get("function") or {}).get("name") for s in TOOL_SCHEMAS}
        required = {
            "find_staff",
            "find_tasks",
            "find_incidents",
            "find_category_owners",
            "find_responsible_people",
            "assign_responsibility",
            "find_establishments",
            "find_documents",
            "retrieve_operational_history",
            "get_dashboard_task",
            "create_dashboard_task",
            "reassign_dashboard_task",
            "update_dashboard_task_status",
        }
        for name in required:
            self.assertIn(name, schema_names, f"missing schema {name}")
            self.assertIn(name, TOOL_REQUIRED_ACTIONS, f"missing rbac {name}")
            self.assertIn(name, CANONICAL_TOOL_NAMES, f"missing canonical {name}")


class AssignAndStatusFlowMocks(SimpleTestCase):
    """Conversation examples via mocked DB services."""

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

    @patch("miya.services.ops.tasks.require_permission", return_value=None)
    @patch("miya.services.ops.tasks.require_restaurant", return_value=None)
    @patch("miya.services.ops.tasks._resolve_task")
    @patch("dashboard.views_agent._resolve_assignee")
    def test_assign_closing_checklist_to_ahmed(self, mock_assignee, mock_task, *_perms):
        from miya.services.ops.tasks import assign_task

        ahmed = MagicMock()
        ahmed.id = "a1"
        ahmed.email = "ahmed@ex.com"
        ahmed.first_name = "Ahmed"
        ahmed.last_name = "Hassan"
        task = MagicMock()
        task.id = "t1"
        task.title = "Closing checklist"
        task.status = "PENDING"
        task.assigned_to = None
        task.assignees = MagicMock()
        mock_task.return_value = (task, None)
        mock_assignee.return_value = (ahmed, None)

        fresh = MagicMock()
        fresh.id = "t1"
        fresh.title = "Closing checklist"
        fresh.status = "PENDING"
        fresh.assigned_to = ahmed
        fresh.assigned_to_id = ahmed.id
        fresh.assignees.filter.return_value.exists.return_value = True
        fresh.due_date = None
        fresh.updated_at = None
        fresh.priority = "MEDIUM"
        fresh.category = ""

        with patch("dashboard.models.Task") as TaskModel, patch(
            "dashboard.task_assign_notify.notify_task_reassignment"
        ), patch("dashboard.task_sync.broadcast_tasks_invalidate"):
            TaskModel.objects.select_related.return_value.filter.return_value.first.return_value = fresh
            result = assign_task(self._ctx(), assignee_name="Ahmed", q="Closing checklist")

        self.assertTrue(result.success)
        self.assertTrue(result.verified)
        task.assignees.clear.assert_called()
        task.assignees.add.assert_called_with(ahmed)

    @patch("miya.services.message_pipeline.claim_mutation_once", return_value=True)
    @patch("miya.services.ops.context.guard_entity_location", return_value=None)
    @patch("miya.services.ops.tasks.require_permission", return_value=None)
    @patch("miya.services.ops.tasks.require_restaurant", return_value=None)
    @patch("miya.services.ops.tasks._resolve_task")
    def test_update_status_verify_fail_not_done(self, mock_task, *_perms):
        from miya.services.ops.tasks import update_task_status

        task = MagicMock()
        task.id = "t1"
        task.title = "Closing checklist"
        task.status = "PENDING"
        task.routing_metadata = {}
        mock_task.return_value = (task, None)

        stale = MagicMock()
        stale.id = "t1"
        stale.status = "PENDING"  # verify fails
        stale.assigned_to = None
        stale.due_date = None
        stale.updated_at = None
        stale.priority = "MEDIUM"
        stale.category = ""
        stale.title = "Closing checklist"

        with patch("dashboard.models.Task") as TaskModel, patch(
            "dashboard.task_sync.broadcast_tasks_invalidate"
        ):
            task_qs = MagicMock()
            task_qs.filter.return_value.first.return_value = stale
            TaskModel.objects.select_related = MagicMock(return_value=task_qs)
            result = update_task_status(self._ctx(), status="COMPLETED", q="Closing checklist")

        body = result.as_tool_response()
        self.assertFalse(body["success"])
        self.assertEqual(body["code"], "verify_failed")
        self.assertIn("Do NOT tell the user the action succeeded", body["miya_directive"])
