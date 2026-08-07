"""Phase 12 — unified intent, paraphrase, entity resolution, multi-channel parity."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from miya.services.intelligence.copilot.understand import (
    is_briefing_query,
    is_operational_search_query,
    understand_turn,
)
from miya.services.intelligence.entity_resolver import parse_ordinal_reference
from miya.services.intelligence.eval.cases import ALL_EVAL_CASES
from miya.services.intelligence.eval.cases.phase12_expansion import PHASE12_EXPANSION_CASES
from miya.services.intelligence.paraphrase_lexicon import apply_paraphrase_lexicon
from miya.services.intelligence.planning.classify import classify_message
from miya.services.intelligence.planning.compound import detect_compound_intent
from miya.services.intelligence.planning.types import ClassifiedIntent, EntityType, IntentClass
from miya.services.intelligence.unified_understand import unified_understand


class MultiChannelParityTests(SimpleTestCase):
    """Same semantic request → same intent across channels."""

    MESSAGE = "Assign Ahmed the closing task."

    def test_dashboard_whatsapp_mobile_voice_same_intent(self):
        intents = []
        for ch in ("dashboard", "whatsapp", "mobile", "voice"):
            c = unified_understand(self.MESSAGE, channel=ch)
            intents.append(c.intent)
        self.assertEqual(len(set(intents)), 1)
        self.assertEqual(intents[0], IntentClass.ASSIGN)

    def test_understand_turn_uses_unified_path(self):
        c = understand_turn(self.MESSAGE, channel="whatsapp")
        self.assertEqual(c.intent, IntentClass.ASSIGN)
        self.assertIn("channel:whatsapp", c.reasons)


class ParaphraseLexiconTests(SimpleTestCase):
    def _classified(self, text: str) -> ClassifiedIntent:
        return classify_message(text)

    def test_close_decoration_task(self):
        c = apply_paraphrase_lexicon(self._classified("Close the decoration task."))
        self.assertEqual(c.intent, IntentClass.COMPLETE)
        self.assertEqual(c.entity_type, EntityType.TASK)

    def test_ahmed_finished_closing(self):
        c = apply_paraphrase_lexicon(self._classified("Ahmed finished the closing."))
        self.assertEqual(c.intent, IntentClass.COMPLETE)

    def test_put_ahmed_on_closing(self):
        c = apply_paraphrase_lexicon(self._classified("Put Ahmed on closing."))
        self.assertEqual(c.intent, IntentClass.ASSIGN)
        self.assertEqual(c.assignee_hint.lower(), "ahmed")

    def test_freezer_broken_creates_incident(self):
        c = apply_paraphrase_lexicon(self._classified("The freezer is broken."))
        self.assertEqual(c.intent, IntentClass.CREATE)
        self.assertEqual(c.entity_type, EntityType.INCIDENT)

    def test_forward_to_hr_routes(self):
        c = apply_paraphrase_lexicon(self._classified("Forward this to HR."))
        self.assertEqual(c.intent, IntentClass.ROUTE)

    def test_show_insurance(self):
        c = apply_paraphrase_lexicon(self._classified("Show me the insurance."))
        self.assertEqual(c.intent, IntentClass.RETRIEVE)
        self.assertEqual(c.entity_type, EntityType.DOCUMENT)

    def test_schedule_meeting_hr(self):
        c = apply_paraphrase_lexicon(self._classified("Schedule a meeting with HR."))
        self.assertEqual(c.intent, IntentClass.SCHEDULE)
        self.assertEqual(c.entity_type, EntityType.MEETING)


class CompoundIntentTests(SimpleTestCase):
    def test_complete_and_tell_manager(self):
        msg = "Complete Ahmed's closing task and tell the manager."
        c = classify_message(msg)
        plan = detect_compound_intent(msg, c)
        self.assertIsNotNone(plan)
        self.assertTrue(plan.compound)
        self.assertEqual(len(plan.steps), 2)
        self.assertEqual(plan.steps[0].action, "complete_task")
        self.assertEqual(plan.steps[1].action, "notify_manager_urgent")


class OrdinalReferenceTests(SimpleTestCase):
    def test_second_one(self):
        self.assertEqual(parse_ordinal_reference("Assign the second one to Ahmed."), 1)

    def test_first_one(self):
        self.assertEqual(parse_ordinal_reference("Mark the first one complete."), 0)


class BriefingGuardExpansionTests(SimpleTestCase):
    """50+ entity-specific history queries must not become briefing."""

    ENTITY_TEMPLATES = [
        "What happened to {e}?",
        "What happened with the {e}?",
        "Who changed the {e}?",
        "When was the {e} completed?",
        "What is the status of {e}?",
    ]
    ENTITIES = [
        "Maxime's photos",
        "freezer incident",
        "closing checklist",
        "insurance document",
        "ABC Foods invoice",
        "decoration task",
        "Ahmed's shift",
        "kitchen meeting",
        "payroll request",
        "maintenance ticket",
    ]

    def test_entity_history_not_briefing(self):
        failures = []
        for tmpl in self.ENTITY_TEMPLATES:
            for ent in self.ENTITIES:
                msg = tmpl.format(e=ent)
                if is_briefing_query(msg):
                    failures.append(msg)
        self.assertEqual(failures, [], f"Briefing hijacked: {failures[:5]}")

    def test_entity_history_routes_search(self):
        for msg in (
            "What happened to Maxime's photos?",
            "Who changed the decoration task?",
            "When was the closing checklist completed?",
        ):
            c = understand_turn(msg)
            self.assertTrue(
                is_operational_search_query(msg, c),
                msg,
            )


class AssistantReentryTests(SimpleTestCase):
    """Assistant text must never become executable user commands."""

    ASSISTANT_LINES = [
        "Done — task completed.",
        "I've assigned Ahmed to the closing task.",
        "Approved. The invoice is marked paid.",
        "Which Decoration task do you mean?",
    ]

    def test_sanitize_keeps_assistant_role(self):
        from miya.services.message_pipeline import sanitize_history

        for line in self.ASSISTANT_LINES:
            out = sanitize_history([{"role": "assistant", "content": line}])
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0]["role"], "assistant")

    def test_pipeline_meta_marks_non_executable(self):
        from miya.services.message_pipeline import TurnContext, attach_pipeline_meta

        for line in self.ASSISTANT_LINES:
            turn = TurnContext(message_id="m1", conversation_id="c1", user_id="u1", channel="whatsapp")
            result = attach_pipeline_meta({}, turn, line)
            self.assertTrue(result.get("assistant_text_is_not_executable"), line)


class EvalSuiteExpansionTests(SimpleTestCase):
    def test_phase12_expansion_minimum_count(self):
        self.assertGreaterEqual(len(PHASE12_EXPANSION_CASES), 260)

    def test_total_eval_cases_minimum_300(self):
        self.assertGreaterEqual(len(ALL_EVAL_CASES), 300)


class EntityResolverClarifyTests(SimpleTestCase):
    @patch("miya.services.ops.tasks.get_task_state")
    def test_ambiguous_tasks_clarify_not_guess(self, mock_state):
        from miya.services.intelligence.entity_resolver import resolve_entity_reference
        from miya.services.ops.context import OpsContext
        from miya.services.ops.result import OpsResult

        mock_state.return_value = OpsResult(
            success=False,
            needs_clarification=True,
            message_for_user="Which Decoration task do you mean?",
            data={"candidates": [{"id": "t1"}, {"id": "t2"}, {"id": "t3"}]},
        )
        ctx = OpsContext(
            user=MagicMock(),
            restaurant=MagicMock(id=1),
            user_id="u1",
            restaurant_id="r1",
            location_id="loc1",
            role="MANAGER",
        )
        ref = resolve_entity_reference(ctx, entity_type="task", query="Decoration")
        self.assertTrue(ref.needs_clarify)
        self.assertEqual(len(ref.candidates or []), 3)
        self.assertIn("Which", ref.clarify_message)
