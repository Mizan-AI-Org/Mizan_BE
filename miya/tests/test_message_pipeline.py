"""Architecture: Miya NL replies must never trigger mutations."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings


class SanitizeHistoryTests(SimpleTestCase):
    def test_keeps_user_and_assistant_only(self):
        from miya.services.message_pipeline import sanitize_history

        out = sanitize_history(
            [
                {"role": "user", "content": "Close decoration"},
                {"role": "assistant", "content": "Done — marked completed."},
                {"role": "tool", "content": '{"success": true}'},
                {"role": "system", "content": "ignore me"},
                {"role": "USER", "content": "  ok  "},
                {"role": "assistant", "content": ""},
            ]
        )
        self.assertEqual(
            out,
            [
                {"role": "user", "content": "Close decoration"},
                {"role": "assistant", "content": "Done — marked completed."},
                {"role": "user", "content": "ok"},
            ],
        )

    def test_never_promotes_assistant_to_user(self):
        from miya.services.message_pipeline import sanitize_history

        out = sanitize_history(
            [{"role": "assistant", "content": "I've completed the decoration task."}]
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["role"], "assistant")


class TurnLifecycleTests(SimpleTestCase):
    def test_finalize_terminates_and_marks_non_executable(self):
        from miya.services.message_pipeline import (
            ExecutionStage,
            TurnContext,
            attach_pipeline_meta,
        )

        turn = TurnContext(
            message_id="msg-1",
            conversation_id="conv-1",
            user_id="u1",
            channel="dashboard",
        )
        turn.advance(ExecutionStage.AGENT_REASONING)
        op = turn.record_tool_call(
            tool_name="update_dashboard_task_status",
            arguments={"task_id": "123", "status": "COMPLETED"},
            tool_call_id="tc-1",
        )
        turn.record_tool_result(
            tool_name="update_dashboard_task_status",
            tool_call_id="tc-1",
            operation_id=op,
            result={"success": True, "code": "ok"},
        )
        result = attach_pipeline_meta({"reply": "raw"}, turn, "Done — decoration completed.")
        self.assertTrue(result["assistant_text_is_not_executable"])
        self.assertEqual(result["execution_stage"], "END")
        self.assertTrue(turn.terminated)
        with self.assertRaises(RuntimeError):
            turn.advance(ExecutionStage.TOOL_CALL)

    def test_assert_user_initiated_blocks_after_reasoning(self):
        from miya.services.message_pipeline import (
            ExecutionStage,
            TurnContext,
            assert_user_initiated,
        )

        turn = TurnContext(message_id="m", conversation_id="c")
        assert_user_initiated(turn.stage, for_action="fast_path")
        turn.advance(ExecutionStage.AGENT_REASONING)
        with self.assertRaises(RuntimeError):
            assert_user_initiated(turn.stage, for_action="fast_path")


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "miya-pipeline-tests",
        }
    }
)
class IdempotencyTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_claim_mutation_once_suppresses_duplicate(self):
        from miya.services.message_pipeline import claim_mutation_once

        self.assertTrue(claim_mutation_once("op-test-1"))
        self.assertFalse(claim_mutation_once("op-test-1"))
        self.assertTrue(claim_mutation_once("op-test-2"))

    def test_update_task_status_duplicate_does_not_save_twice(self):
        from miya.services.ops.context import OpsContext
        from miya.services.ops.tasks import update_task_status

        user = MagicMock()
        user.id = "u1"
        user.pk = "u1"
        user.role = "MANAGER"
        rest = MagicMock()
        rest.id = "r1"
        ctx = OpsContext(
            user=user,
            restaurant=rest,
            restaurant_id="r1",
            user_id="u1",
            role="MANAGER",
            channel="dashboard",
            language="en",
        )
        task = MagicMock()
        task.id = "123"
        task.title = "Decoration"
        task.status = "IN_PROGRESS"
        task.assigned_to = None
        task.due_date = None
        task.updated_at = None
        task.priority = "MEDIUM"
        task.category = ""
        task.completed_at = None
        task.completed_by = None
        task.routing_metadata = {}

        fresh = MagicMock()
        fresh.id = "123"
        fresh.title = "Decoration"
        fresh.status = "COMPLETED"
        fresh.assigned_to = None
        fresh.due_date = None
        fresh.updated_at = None
        fresh.priority = "MEDIUM"
        fresh.category = ""

        with patch("miya.services.ops.tasks.require_restaurant", return_value=None), patch(
            "miya.services.ops.tasks.require_task_status_permission", return_value=None
        ), patch("miya.services.ops.tasks._resolve_task", return_value=(task, None)), patch(
            "dashboard.task_sync.broadcast_tasks_invalidate"
        ), patch("dashboard.models.Task") as TaskModel:
            TaskModel.objects.select_related.return_value.filter.return_value.first.return_value = fresh
            first = update_task_status(
                ctx,
                status="COMPLETED",
                task_id="123",
                operation_id="op-decoration-complete-1",
            )
            self.assertTrue(first.success)
            self.assertEqual(task.save.call_count, 1)

            task.status = "COMPLETED"
            second = update_task_status(
                ctx,
                status="COMPLETED",
                task_id="123",
                operation_id="op-decoration-complete-1",
            )
            self.assertTrue(second.success)
            self.assertTrue((second.data or {}).get("deduplicated") or (second.data or {}).get("idempotent"))
            self.assertEqual(task.save.call_count, 1)


class SendAnnouncementNoNlRewriteTests(SimpleTestCase):
    def test_vague_audience_returns_structured_tool_required(self):
        from miya.services.tools import execute_tool

        user = MagicMock()
        user.role = "MANAGER"
        with patch("miya.services.tools.allowed_tools_for_user", return_value={"send_announcement"}), patch(
            "miya.services.tools.resolve_active_tenant", return_value=None
        ):
            result = execute_tool(
                "send_announcement",
                {"audience": "someone", "message": "prepare buffet"},
                access_token=None,
                session_context={"restaurant_id": "r1", "role": "MANAGER"},
                user=user,
            )
        self.assertFalse(result.get("success"))
        self.assertEqual(result.get("code"), "structured_tool_required")


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "miya-pipeline-chat-tests",
        }
    }
)
class RunMiyaChatPipelineTests(SimpleTestCase):
    """One user message → one tool call → one DB update → final reply does not re-enter."""

    def setUp(self):
        cache.clear()

    def _exec_ctx(self, message_id="msg-test"):
        from miya.services.intelligence.context_engine import ExecutionContext

        return ExecutionContext(
            user_id="u1",
            organization_id="",
            role="MANAGER",
            conversation_id="conv-1",
            message_id=message_id,
            channel="dashboard",
            locale="en",
            current_time="2026-08-07T12:00:00+00:00",
        )

    @patch("miya.services.mastra_client.mastra_enabled", return_value=False)
    @patch("miya.services.agent._openai_chat")
    @patch("miya.services.agent.execute_tool")
    @patch("miya.services.agent.tools_for_user", return_value=[{"type": "function", "function": {"name": "update_dashboard_task_status"}}])
    @patch("miya.services.agent.build_system_prompt", return_value="sys")
    @patch("miya.services.agent.build_session_context")
    @patch("miya.services.intelligence.context_engine.build_execution_context")
    def test_complete_decoration_once_then_final_response_ends(
        self,
        mock_exec_ctx,
        mock_ctx,
        _prompt,
        _tools,
        mock_exec,
        mock_openai,
        _mastra,
    ):
        from miya.services.agent import run_miya_chat

        mock_exec_ctx.return_value = self._exec_ctx("msg-test-decoration")
        mock_ctx.return_value = {
            "user_id": "u1",
            "restaurant_id": None,
            "language": "en",
            "role": "MANAGER",
            "thread_id": "dash-1",
        }
        mock_exec.return_value = {
            "success": True,
            "verified": True,
            "code": "ok",
            "data": {
                "task": {"id": "123", "title": "Decoration", "status": "COMPLETED"},
                "operation": "update_task_status",
                "new_status": "COMPLETED",
            },
        }
        mock_openai.side_effect = [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "tc-1",
                                    "type": "function",
                                    "function": {
                                        "name": "update_dashboard_task_status",
                                        "arguments": '{"task_id":"123","status":"COMPLETED"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Done — the decoration task has been marked as completed.",
                        }
                    }
                ]
            },
        ]

        user = MagicMock()
        user.id = "u1"
        user.role = "MANAGER"

        with patch("miya.services.agent._try_payroll_delegation_fast_path", return_value=None), patch(
            "miya.services.agent._try_staff_delegation_fast_path", return_value=None
        ), patch("miya.services.agent._try_schedule_fast_path", return_value=None), patch(
            "miya.services.agent._try_ambiguous_assign_fast_path", return_value=None
        ), patch("miya.services.agent._try_entity_status_fast_path", return_value=None), patch(
            "miya.services.agent._try_pending_ops_fast_path", return_value=None
        ), patch("miya.services.agent._try_manager_schedule_fast_path", return_value=None):
            result = run_miya_chat(
                user=user,
                access_token=None,
                user_message="Close the decoration task, it's done.",
                history=[
                    {
                        "role": "assistant",
                        "content": "I found four tasks. The first one appears relevant.",
                    }
                ],
                channel="dashboard",
                inbound_message_id="msg-test-decoration",
            )

        self.assertEqual(mock_exec.call_count, 1)
        name, args = mock_exec.call_args[0][0], mock_exec.call_args[0][1]
        self.assertEqual(name, "update_dashboard_task_status")
        self.assertEqual(args.get("task_id"), "123")
        self.assertEqual(args.get("status"), "COMPLETED")
        self.assertIn("_operation_id", args)
        self.assertTrue(result.get("assistant_text_is_not_executable"))
        self.assertEqual(result.get("execution_stage"), "END")
        self.assertIn("decoration", (result.get("reply") or "").lower())
        self.assertEqual(result.get("pipeline", {}).get("tool_call_count"), 1)

        # Feeding the final reply back as history must not mutate again by itself
        self.assertEqual(mock_openai.call_count, 2)

    @patch("miya.services.mastra_client.mastra_enabled", return_value=False)
    @patch("miya.services.agent._openai_chat")
    @patch("miya.services.agent.execute_tool")
    @patch("miya.services.agent.tools_for_user", return_value=[])
    @patch("miya.services.agent.build_system_prompt", return_value="sys")
    @patch("miya.services.agent.build_session_context")
    @patch("miya.services.intelligence.context_engine.build_execution_context")
    def test_assistant_history_does_not_execute_tools(
        self,
        mock_exec_ctx,
        mock_ctx,
        _prompt,
        _tools,
        mock_exec,
        mock_openai,
        _mastra,
    ):
        """Assistant NL in history is context only — no mutation without a user tool call."""
        from miya.services.agent import run_miya_chat

        mock_exec_ctx.return_value = self._exec_ctx("msg-thanks")
        mock_ctx.return_value = {
            "user_id": "u1",
            "restaurant_id": None,
            "language": "en",
            "role": "MANAGER",
        }
        mock_openai.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "How can I help?"}}]
        }
        user = MagicMock()
        user.id = "u1"
        user.role = "MANAGER"

        with patch("miya.services.agent._try_payroll_delegation_fast_path", return_value=None), patch(
            "miya.services.agent._try_staff_delegation_fast_path", return_value=None
        ), patch("miya.services.agent._try_schedule_fast_path", return_value=None), patch(
            "miya.services.agent._try_ambiguous_assign_fast_path", return_value=None
        ), patch("miya.services.agent._try_entity_status_fast_path", return_value=None), patch(
            "miya.services.agent._try_pending_ops_fast_path", return_value=None
        ), patch("miya.services.agent._try_manager_schedule_fast_path", return_value=None):
            result = run_miya_chat(
                user=user,
                access_token=None,
                user_message="thanks",
                history=[
                    {
                        "role": "assistant",
                        "content": "I've completed the decoration task.",
                    }
                ],
                channel="dashboard",
            )

        mock_exec.assert_not_called()
        self.assertEqual(result.get("execution_stage"), "END")
        self.assertTrue(result.get("assistant_text_is_not_executable"))
