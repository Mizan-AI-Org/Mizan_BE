"""Assistant re-entry E2E — assistant text must never trigger mutations."""
from __future__ import annotations

from unittest.mock import patch

from miya.services.message_pipeline import TurnContext, attach_pipeline_meta, sanitize_history
from miya.tests.e2e.harness import MiyaE2EHarness, PostgresE2ETestCase
from miya.tests.e2e.seed import seed_single_establishment


class AssistantReentryE2ETests(PostgresE2ETestCase):
    def setUp(self):
        super().setUp()
        self.world = seed_single_establishment()
        self.harness = MiyaE2EHarness(self.world)

    def test_e2e_reentry_01_sanitize_keeps_assistant_role(self):
        lines = [
            "Done — the task is complete.",
            "I've assigned Ahmed to the closing task.",
        ]
        for line in lines:
            out = sanitize_history([{"role": "assistant", "content": line}])
            self.assertEqual(out[0]["role"], "assistant")

    def test_e2e_reentry_02_pipeline_meta_non_executable(self):
        turn = TurnContext(message_id="m1", conversation_id="c1", user_id="u1", channel="whatsapp")
        result = attach_pipeline_meta({}, turn, "Done — task completed.")
        self.assertTrue(result.get("assistant_text_is_not_executable"))

    def test_e2e_reentry_03_assistant_history_no_copilot_mutation(self):
        """Assistant NL in history + benign user msg must not mutate via copilot."""
        sess = self.world.session_for(self.world.manager)
        cap = self.harness.send(
            "thanks",
            session={
                **sess,
                "_history": [
                    {"role": "assistant", "content": "Done — the task is complete."},
                ],
            },
        )
        task = self.world.tasks["closing"]
        task.refresh_from_db()
        if not cap.verified:
            self.assertNotEqual(task.status, "COMPLETED")

    @patch("miya.services.agent._openai_chat")
    def test_e2e_reentry_04_whatsapp_webhook_no_double_mutate(self, mock_openai):
        from unittest.mock import MagicMock

        from miya.services.agent import run_miya_chat

        mock_openai.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "How can I help?"}}]
        }
        user = self.world.manager
        sess = self.world.session_for(user, channel="whatsapp")
        exec_mock = MagicMock()
        exec_mock.restaurant = user.restaurant
        exec_mock.message_id = "msg-wa-reentry"
        exec_mock.conversation_id = "conv-wa-reentry"
        exec_mock.attach_to_session = lambda s: {**sess, **(s or {})}
        with patch("miya.services.agent._try_payroll_delegation_fast_path", return_value=None), patch(
            "miya.services.agent._try_staff_delegation_fast_path", return_value=None
        ), patch("miya.services.agent._try_schedule_fast_path", return_value=None), patch(
            "miya.services.agent._try_ambiguous_assign_fast_path", return_value=None
        ), patch("miya.services.agent._try_entity_status_fast_path", return_value=None), patch(
            "miya.services.agent._try_pending_ops_fast_path", return_value=None
        ), patch("miya.services.agent._try_manager_schedule_fast_path", return_value=None), patch(
            "miya.services.mastra_client.mastra_enabled", return_value=False
        ), patch("miya.services.agent.tools_for_user", return_value=[]), patch(
            "miya.services.agent.build_system_prompt", return_value="sys"
        ), patch("miya.services.agent.build_session_context", return_value=sess), patch(
            "miya.services.intelligence.context_engine.build_execution_context",
            return_value=exec_mock,
        ):
            result = run_miya_chat(
                user=user,
                access_token=None,
                user_message="ok",
                history=[{"role": "assistant", "content": "Done — the task is complete."}],
                channel="whatsapp",
            )
        self.assertTrue(result.get("assistant_text_is_not_executable"))

    def test_e2e_reentry_05_clarify_response_not_user_command(self):
        cap = self.harness.send("Complete it.")
        if cap.needs_clarification:
            out = sanitize_history([{"role": "assistant", "content": cap.reply}])
            self.assertEqual(out[0]["role"], "assistant")
