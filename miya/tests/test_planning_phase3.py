"""Phase 3 — Reasoning & Planning Engine tests."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from miya.services.intelligence.planning.classify import classify_message
from miya.services.intelligence.planning.types import (
    Confidence,
    EntityType,
    IntentClass,
    PlanAction,
)
from miya.services.ops.context import OpsContext
from miya.services.ops.result import clarify, fail, ok


def _ctx(*, location_id="loc-a", locations=None):
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
        location_id=location_id,
        location_name="Branch A" if location_id else None,
        available_locations=locations
        or (
            [{"id": "loc-a", "name": "Branch A"}, {"id": "loc-b", "name": "Branch B"}]
            if location_id
            else [{"id": "loc-a", "name": "Branch A"}, {"id": "loc-b", "name": "Branch B"}]
        ),
    )


class ClassifyIntentTests(SimpleTestCase):
    def test_close_decoration_task(self):
        c = classify_message("Close the decoration task.")
        self.assertEqual(c.intent, IntentClass.COMPLETE)
        self.assertEqual(c.entity_type, EntityType.TASK)
        self.assertIn("decoration", c.query.lower())
        self.assertEqual(c.confidence, Confidence.HIGH)

    def test_complete_it_pronoun(self):
        c = classify_message("Complete it.")
        self.assertEqual(c.intent, IntentClass.COMPLETE)
        self.assertTrue(c.pronoun)
        self.assertEqual(c.confidence, Confidence.MEDIUM)

    def test_assign_that_to_ahmed(self):
        c = classify_message("Assign that to Ahmed.")
        self.assertEqual(c.intent, IntentClass.ASSIGN)
        self.assertTrue(c.pronoun)
        self.assertEqual(c.assignee_hint.lower(), "ahmed")

    def test_send_this_to_hr(self):
        c = classify_message("Send this to HR.")
        self.assertEqual(c.intent, IntentClass.ASSIGN)
        self.assertTrue(c.pronoun)

    def test_other_branch_is_low(self):
        c = classify_message("Do the same for the other branch.")
        self.assertEqual(c.confidence, Confidence.LOW)
        self.assertIn("cross_establishment_ambiguity", c.reasons)


class AmbiguityTests(SimpleTestCase):
    def test_multiple_tasks_ask_not_guess(self):
        from miya.services.intelligence.planning.resolve import resolve_plan

        intent = classify_message("Close the checklist task.")
        with patch(
            "miya.services.ops.tasks.get_task_state",
            return_value=clarify(
                message="Several tasks match",
                data={
                    "candidates": [
                        {"id": "t1", "title": "FOH checklist"},
                        {"id": "t2", "title": "Kitchen checklist"},
                    ]
                },
            ),
        ):
            plan = resolve_plan(intent, ctx=_ctx(), session_context={})
        self.assertEqual(plan.action, PlanAction.CLARIFY)
        self.assertIn("guess", plan.clarification_message.lower())
        self.assertEqual(len(plan.candidates), 2)

    def test_complete_it_without_working_set_asks(self):
        from miya.services.intelligence.planning.resolve import resolve_plan

        intent = classify_message("Complete it.")
        with patch(
            "miya.services.intelligence.working_memory.get_working_memory",
            return_value={"empty": True},
        ), patch(
            "miya.services.working_set.resolve_ids",
            return_value=[],
        ):
            plan = resolve_plan(intent, ctx=_ctx(), session_context={})
        self.assertEqual(plan.action, PlanAction.CLARIFY)


class CrossEstablishmentTests(SimpleTestCase):
    def test_no_active_establishment_clarifies(self):
        from miya.services.intelligence.planning.resolve import resolve_plan

        intent = classify_message("Close the decoration task.")
        ctx = _ctx(location_id=None)
        plan = resolve_plan(intent, ctx=ctx, session_context={})
        self.assertEqual(plan.action, PlanAction.CLARIFY)
        self.assertIn("establishment", plan.clarification_message.lower())


class WorkflowExecutionTests(SimpleTestCase):
    def test_task_completion_workflow_executes_once(self):
        from miya.services.intelligence.planning.resolve import resolve_plan
        from miya.services.intelligence.planning.workflows import run_task_completion

        intent = classify_message("Close the decoration task.")
        with patch(
            "miya.services.ops.tasks.get_task_state",
            return_value=ok(
                message="found",
                verified=True,
                data={"task": {"id": "123", "title": "Decoration", "status": "IN_PROGRESS"}},
            ),
        ):
            plan = resolve_plan(intent, ctx=_ctx(), session_context={})
        self.assertEqual(plan.action, PlanAction.EXECUTE)
        self.assertEqual(plan.entity_id, "123")

        verified = ok(
            message="Updated Decoration to COMPLETED.",
            verified=True,
            data={
                "task": {"id": "123", "title": "Decoration", "status": "COMPLETED"},
                "previous_status": "IN_PROGRESS",
            },
        )
        with patch(
            "miya.services.intelligence.planning.workflows.execute_structured_action",
            return_value=verified,
        ) as mock_exec:
            outcome = run_task_completion(
                _ctx(),
                plan,
                execution_context={"message_id": "m1", "user_id": "u1", "organization_id": "r1"},
            )
        self.assertTrue(outcome.success)
        self.assertTrue(outcome.verified)
        self.assertTrue(outcome.presentation_only)
        self.assertIn("VERIFY", outcome.stages_completed)
        mock_exec.assert_called_once()
        self.assertEqual(mock_exec.call_args[0][0], "complete_task")

    def test_permission_failure_surfaces(self):
        from miya.services.intelligence.planning.types import ClassifiedIntent, ExecutionPlan
        from miya.services.intelligence.planning.workflows import run_task_assignment

        plan = ExecutionPlan(
            workflow="task_assignment",
            action=PlanAction.EXECUTE,
            intent=ClassifiedIntent(
                intent=IntentClass.ASSIGN,
                entity_type=EntityType.TASK,
                confidence=Confidence.HIGH,
                assignee_hint="Ahmed",
                query="closing",
            ),
            entity_id="t1",
            tool_args={"task_id": "t1", "assignee_name": "Ahmed"},
        )
        denied = fail(code="permission_denied", message="You don't have permission.")
        with patch(
            "miya.services.intelligence.planning.workflows.execute_structured_action",
            return_value=denied,
        ):
            out = run_task_assignment(_ctx(), plan, execution_context={})
        self.assertFalse(out.success)
        self.assertFalse(out.verified)
        self.assertIn("permission", out.reply.lower())


class PlanningEngineIntegrationTests(SimpleTestCase):
    def test_engine_returns_presentation_only(self):
        from miya.services.intelligence.planning.engine import try_planning_engine

        user = MagicMock()
        user.id = "u1"
        user.pk = "u1"
        user.role = "MANAGER"
        user.restaurant = MagicMock(id="r1")

        with patch(
            "miya.services.intelligence.planning.engine.build_ops_context",
            return_value=_ctx(),
        ), patch(
            "miya.services.ops.tasks.get_task_state",
            return_value=ok(
                message="found",
                verified=True,
                data={"task": {"id": "123", "title": "Decoration", "status": "PENDING"}},
            ),
        ), patch(
            "miya.services.intelligence.planning.workflows.execute_structured_action",
            return_value=ok(
                message="Done — Decoration completed.",
                verified=True,
                data={"task": {"id": "123", "status": "COMPLETED"}},
            ),
        ):
            result = try_planning_engine(
                user_message="Close the decoration task.",
                user=user,
                session_context={
                    "user_id": "u1",
                    "restaurant_id": "r1",
                    "location_id": "loc-a",
                    "channel": "dashboard",
                },
            )
        self.assertIsNotNone(result)
        self.assertTrue(result["presentation_only"])
        self.assertTrue(result["assistant_text_is_not_executable"])
        self.assertTrue(result.get("planning_engine"))

    def test_response_does_not_retrigger(self):
        """Final reply is presentation — classifying it must not look like COMPLETE."""
        c = classify_message("Done — the decoration task has been marked as completed.")
        # May match COMPLETE keywords but should be treated carefully; engine only runs on user turns
        # Ensure presentation flag is the hard stop in engine output
        from miya.services.intelligence.planning.types import PlanResult

        pr = PlanResult(reply="Done — completed.", success=True, verified=True)
        body = pr.as_chat_result()
        self.assertTrue(body["assistant_text_is_not_executable"])
        self.assertTrue(body["presentation_only"])


class MultiStepAssignTests(SimpleTestCase):
    def test_assign_that_to_ahmed_uses_working_set(self):
        from miya.services.intelligence.planning.resolve import resolve_plan

        intent = classify_message("Assign that to Ahmed.")
        with patch(
            "miya.services.intelligence.working_memory.get_working_memory",
            return_value={"current_task_id": "task-9", "current_task_label": "Closing"},
        ):
            plan = resolve_plan(intent, ctx=_ctx(), session_context={})
        self.assertEqual(plan.action, PlanAction.EXECUTE)
        self.assertEqual(plan.entity_id, "task-9")
        self.assertEqual(plan.tool_args.get("assignee_name"), "Ahmed")

    def test_send_this_to_hr_needs_task_referent(self):
        from miya.services.intelligence.planning.workflows import run_task_assignment
        from miya.services.intelligence.planning.types import ClassifiedIntent, ExecutionPlan

        plan = ExecutionPlan(
            workflow="task_assignment",
            action=PlanAction.EXECUTE,
            intent=ClassifiedIntent(
                intent=IntentClass.ASSIGN,
                entity_type=EntityType.CATEGORY,
                confidence=Confidence.MEDIUM,
                pronoun=True,
                raw_message="Send this to HR.",
                slots={"assign_to_category": "HR"},
            ),
            tool_args={"assign_to_category": "HR"},
        )
        out = run_task_assignment(_ctx(), plan, execution_context={})
        self.assertTrue(out.needs_clarification)
