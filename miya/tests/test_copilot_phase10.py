"""Phase 10 — Miya Operational Copilot integration tests."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from miya.services.intelligence.copilot import (
    MANDATORY_MUTATION_STAGES,
    CopilotStage,
    is_mutation_intent,
    is_operational_search_query,
    understand_turn,
)
from miya.services.intelligence.copilot.orchestrator import run_copilot_turn
from miya.services.intelligence.copilot.types import CopilotResult
from miya.services.intelligence.planning.types import IntentClass
from miya.services.ops.result import fail, ok


def _user(*, role="MANAGER"):
    u = MagicMock()
    u.id = "u1"
    u.pk = "u1"
    u.role = role
    u.restaurant_id = "r1"
    return u


def _rest():
    r = MagicMock()
    r.id = "r1"
    r.name = "Mizan Demo"
    return r


def _session(**extra):
    base = {
        "user_id": "u1",
        "restaurant_id": "r1",
        "role": "MANAGER",
        "location_id": "loc-a",
        "location_name": "Casablanca",
        "available_locations": [{"id": "loc-a", "name": "Casablanca"}],
        "_pipeline_message_id": "msg-1",
        "_pipeline_conversation_id": "conv-1",
    }
    base.update(extra)
    return base


class CopilotUnderstandTests(SimpleTestCase):
    """Phase 10 example queries — correct UNDERSTAND routing."""

    def test_close_task_is_mutation_not_search(self):
        c = understand_turn("Complete Ahmed's closing task.")
        self.assertTrue(is_mutation_intent(c))
        self.assertFalse(is_operational_search_query("Complete Ahmed's closing task.", c))

    def test_close_decoration_is_mutation_not_search(self):
        c = understand_turn("Close the decoration task.")
        self.assertEqual(c.intent, IntentClass.COMPLETE)
        self.assertFalse(is_operational_search_query("Close the decoration task.", c))

    def test_what_happened_today_is_search_not_mutation(self):
        c = understand_turn("What happened today?")
        self.assertFalse(is_mutation_intent(c))
        self.assertTrue(is_operational_search_query("What happened today?", c))

    def test_what_needs_attention_is_briefing(self):
        from miya.services.intelligence.copilot.understand import is_briefing_query

        self.assertTrue(is_briefing_query("What needs my attention?"))

    def test_responsible_for_deliveries_is_search(self):
        c = understand_turn("Who is responsible for deliveries?")
        self.assertTrue(is_operational_search_query("Who is responsible for deliveries?", c))

    def test_overdue_tasks_is_search(self):
        c = understand_turn("Which tasks are overdue?")
        self.assertTrue(is_operational_search_query("Which tasks are overdue?", c))

    def test_freezer_incident_history_is_search(self):
        c = understand_turn("What happened with the freezer incident?")
        self.assertTrue(is_operational_search_query("What happened with the freezer incident?", c))

    def test_show_photo_is_search(self):
        c = understand_turn("Show me the photo.")
        self.assertTrue(is_operational_search_query("Show me the photo.", c))

    def test_insurance_expiry_is_search(self):
        c = understand_turn("When does our insurance expire?")
        self.assertTrue(is_operational_search_query("When does our insurance expire?", c))

    def test_invoices_need_approval_is_search(self):
        c = understand_turn("Which invoices need approval?")
        self.assertTrue(is_operational_search_query("Which invoices need approval?", c))

    def test_why_routed_to_hr_is_search(self):
        c = understand_turn("Why was this incident routed to HR?")
        self.assertTrue(is_operational_search_query("Why was this incident routed to HR?", c))

    def test_assign_kitchen_manager_is_mutation(self):
        c = understand_turn("Assign this to the kitchen manager.")
        self.assertTrue(is_mutation_intent(c))

    def test_remind_tomorrow_is_mutation(self):
        c = understand_turn("Remind me tomorrow.")
        self.assertEqual(c.intent, IntentClass.REMIND)

    def test_schedule_meeting_is_mutation(self):
        c = understand_turn("Schedule a meeting with the kitchen team.")
        self.assertEqual(c.intent, IntentClass.SCHEDULE)

    def test_uploaded_invoice_history_is_search(self):
        c = understand_turn("What happened with the invoice I uploaded yesterday?")
        self.assertTrue(is_operational_search_query("What happened with the invoice I uploaded yesterday?", c))


class CopilotPipelineTests(SimpleTestCase):
    def test_mandatory_mutation_stages_defined(self):
        self.assertEqual(len(MANDATORY_MUTATION_STAGES), 8)
        self.assertIn(CopilotStage.VERIFY, MANDATORY_MUTATION_STAGES)
        self.assertIn(CopilotStage.NOTIFY, MANDATORY_MUTATION_STAGES)

    def _run_copilot(self, **kwargs):
        with (
            patch(
                "miya.services.intelligence.copilot.orchestrator._try_establishment",
                return_value=None,
            ),
            patch(
                "miya.services.intelligence.copilot.orchestrator._try_proactive_handle",
                return_value=None,
            ),
            patch("miya.services.ops.build_ops_context") as mock_ctx,
        ):
            mock_ctx.return_value = MagicMock(
                user_id="u1",
                restaurant_id="r1",
                location_id="loc-a",
                role=kwargs.get("session_context", {}).get("role", "MANAGER"),
            )
            return run_copilot_turn(**kwargs)

    def test_mutation_routes_to_planning_not_search(self):
        """Production bug guard: close task must not be answered by search-only."""
        user = _user()
        rest = _rest()
        session = _session()

        planned = CopilotResult(
            reply="Done — decoration completed.",
            success=True,
            verified=True,
            tool_trace=[{"tool": "task_completion", "arguments": {"task_id": "t1"}}],
            handler="planning_engine",
            stages_completed=[
                CopilotStage.PLAN.value,
                CopilotStage.EXECUTE.value,
                CopilotStage.VERIFY.value,
                CopilotStage.RECORD.value,
                CopilotStage.RESPOND.value,
            ],
        )

        search_called = []

        def _search_should_not_run(**_kwargs):
            search_called.append(True)
            return None

        with (
            patch(
                "miya.services.intelligence.copilot.orchestrator._try_planning",
                return_value=planned,
            ),
            patch(
                "miya.services.intelligence.copilot.orchestrator._try_search",
                side_effect=_search_should_not_run,
            ),
            patch(
                "miya.services.intelligence.copilot.orchestrator.authorize_mutation",
                return_value=None,
            ),
        ):
            result = self._run_copilot(
                user=user,
                user_message="Close the decoration task.",
                enriched_message="Close the decoration task.",
                session_context=session,
                restaurant=rest,
                channel="dashboard",
            )

        self.assertIsNotNone(result)
        self.assertEqual(result.handler, "planning_engine")
        self.assertTrue(result.verified)
        self.assertFalse(search_called)

    def test_briefing_routes_to_proactive(self):
        user = _user()
        rest = _rest()
        session = _session()

        with patch(
            "miya.services.intelligence.proactive.on_demand_briefing",
            return_value={"reply": "3 invoices pending, 2 overdue tasks."},
        ):
            result = self._run_copilot(
                user=user,
                user_message="What needs my attention?",
                enriched_message="What needs my attention?",
                session_context=session,
                restaurant=rest,
            )

        self.assertIsNotNone(result)
        self.assertEqual(result.handler, "daily_briefing")
        self.assertIn(CopilotStage.SUMMARIZE.value, result.stages_completed)

    def test_authorize_denied_before_execute(self):
        user = _user(role="STAFF")
        rest = _rest()
        session = _session()

        with patch(
            "miya.services.intelligence.copilot.orchestrator.authorize_mutation",
            return_value=fail(
                code="permission_denied",
                message="You don't have permission to do that in this workspace.",
            ),
        ):
            result = self._run_copilot(
                user=user,
                user_message="Close the decoration task.",
                enriched_message="Close the decoration task.",
                session_context=session,
                restaurant=rest,
            )

        self.assertIsNotNone(result)
        self.assertEqual(result.handler, "authorize_denied")
        self.assertFalse(result.success)
        self.assertIn(CopilotStage.AUTHORIZE.value, result.stages_completed)


class CopilotResultTests(SimpleTestCase):
    def test_presentation_only_flag(self):
        r = CopilotResult(reply="Done.", verified=True, handler="planning_engine")
        extra = r.to_chat_extra()
        self.assertTrue(extra["presentation_only"])
        self.assertTrue(extra["copilot"])
