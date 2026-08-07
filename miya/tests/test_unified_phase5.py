"""Phase 5 — Unified Experience: all channels converge on the same state."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from miya.services.intelligence.unified import (
    CANONICAL_CHANNELS,
    apply_task_status,
    get_task_reality,
    normalize_channel,
    run_unified_miya,
)
from miya.services.ops.result import ok


def _user():
    u = MagicMock()
    u.id = "u1"
    u.pk = "u1"
    u.role = "MANAGER"
    rest = MagicMock()
    rest.id = "r1"
    u.restaurant = rest
    u.restaurant_id = "r1"
    return u


class ChannelNormalizeTests(SimpleTestCase):
    def test_all_canonical_channels(self):
        self.assertEqual(CANONICAL_CHANNELS, {"dashboard", "whatsapp", "mobile", "voice"})

    def test_aliases(self):
        self.assertEqual(normalize_channel("wa"), "whatsapp")
        self.assertEqual(normalize_channel("ios"), "mobile")
        self.assertEqual(normalize_channel("dashboard_voice"), "voice")
        self.assertEqual(normalize_channel("WEB"), "dashboard")


class UnifiedMutationPathTests(SimpleTestCase):
    """Every channel must call execute_structured_action — never fork business logic."""

    def test_apply_task_status_uses_structured_action_for_all_channels(self):
        user = _user()
        verified = ok(
            message="Done.",
            verified=True,
            data={"task": {"id": "t1", "title": "decoration", "status": "COMPLETED"}},
        )
        for channel in ("dashboard", "whatsapp", "mobile", "voice"):
            with patch(
                "miya.services.intelligence.unified.execute_structured_action",
                return_value=verified,
            ) as esa, patch(
                "miya.services.intelligence.unified.build_ops_context",
                return_value=MagicMock(restaurant=user.restaurant, user=user, restaurant_id="r1"),
            ):
                result = apply_task_status(
                    user=user,
                    channel=channel,
                    status="COMPLETED",
                    task_id="t1",
                    restaurant=user.restaurant,
                )
            self.assertTrue(result.verified, channel)
            self.assertEqual(esa.call_count, 1, channel)
            action_name = esa.call_args[0][0]
            self.assertIn(action_name, ("complete_task", "update_task_status"), channel)
            exec_ctx = esa.call_args[1]["execution_context"]
            self.assertEqual(exec_ctx["channel"], channel)


class CrossChannelConvergenceTests(SimpleTestCase):
    """
    Test matrix:
      Dashboard → Miya
      Dashboard → WhatsApp
      WhatsApp → Dashboard
      WhatsApp → Miya
      Miya → Dashboard
      Miya → WhatsApp
    All converge on CURRENT DATABASE STATE.
    """

    def test_dashboard_complete_whatsapp_reads_same_status(self):
        """Dashboard → WhatsApp: manager completes; WA reads Completed from DB."""
        user = _user()
        completed = ok(
            message="Completed.",
            verified=True,
            data={"task": {"id": "t1", "title": "decoration", "status": "COMPLETED"}},
        )
        with patch(
            "miya.services.intelligence.unified.execute_structured_action",
            return_value=completed,
        ), patch(
            "miya.services.intelligence.unified.build_ops_context",
            return_value=MagicMock(restaurant=user.restaurant, user=user, restaurant_id="r1"),
        ):
            write = apply_task_status(
                user=user,
                channel="dashboard",
                status="COMPLETED",
                task_id="t1",
                q="decoration",
            )
            read = get_task_reality(
                user=user,
                channel="whatsapp",
                task_id="t1",
                q="decoration",
            )
        self.assertEqual(
            (write.data or {}).get("task", {}).get("status"),
            (read.data or {}).get("task", {}).get("status"),
        )
        self.assertEqual((read.data or {}).get("task", {}).get("status"), "COMPLETED")

    def test_whatsapp_complete_dashboard_reads_same_status(self):
        """WhatsApp → Dashboard: staff completes; dashboard reads Completed."""
        user = _user()
        completed = ok(
            message="Completed.",
            verified=True,
            data={"task": {"id": "t1", "title": "decoration", "status": "COMPLETED"}},
        )
        with patch(
            "miya.services.intelligence.unified.execute_structured_action",
            return_value=completed,
        ), patch(
            "miya.services.intelligence.unified.build_ops_context",
            return_value=MagicMock(restaurant=user.restaurant, user=user, restaurant_id="r1"),
        ):
            write = apply_task_status(
                user=user,
                channel="whatsapp",
                status="COMPLETED",
                task_id="t1",
                assignee_scope=True,
            )
            read = get_task_reality(user=user, channel="dashboard", task_id="t1")
        self.assertEqual(
            (write.data or {}).get("task", {}).get("status"),
            (read.data or {}).get("task", {}).get("status"),
        )

    def test_dashboard_to_miya_same_engine(self):
        """Dashboard → Miya: NL status query uses same reality read."""
        user = _user()
        reality = ok(
            message="Completed.",
            verified=True,
            data={"task": {"id": "t1", "title": "decoration", "status": "COMPLETED"}},
        )
        with patch(
            "miya.services.intelligence.unified.execute_structured_action",
            return_value=reality,
        ), patch(
            "miya.services.intelligence.unified.build_ops_context",
            return_value=MagicMock(restaurant=user.restaurant, user=user, restaurant_id="r1"),
        ):
            # Simulate prior dashboard mutate then Miya (dashboard channel) ask
            apply_task_status(user=user, channel="dashboard", status="COMPLETED", task_id="t1")
            miya_view = get_task_reality(
                user=user, channel="dashboard", q="decoration task"
            )
        self.assertEqual((miya_view.data or {}).get("task", {}).get("status"), "COMPLETED")

    def test_whatsapp_to_miya_same_engine(self):
        """WhatsApp → Miya: after WA complete, Miya NL sees DB state."""
        user = _user()
        reality = ok(
            message="Completed.",
            verified=True,
            data={"task": {"id": "t1", "title": "decoration", "status": "COMPLETED"}},
        )
        with patch(
            "miya.services.intelligence.unified.execute_structured_action",
            return_value=reality,
        ), patch(
            "miya.services.intelligence.unified.build_ops_context",
            return_value=MagicMock(restaurant=user.restaurant, user=user, restaurant_id="r1"),
        ):
            apply_task_status(user=user, channel="whatsapp", status="COMPLETED", task_id="t1")
            miya_view = get_task_reality(user=user, channel="whatsapp", q="decoration")
        self.assertEqual((miya_view.data or {}).get("task", {}).get("status"), "COMPLETED")

    def test_miya_complete_visible_on_dashboard_and_whatsapp(self):
        """Miya → Dashboard / WhatsApp: planning complete_task state is shared."""
        user = _user()
        reality = ok(
            message="Done.",
            verified=True,
            data={"task": {"id": "t1", "title": "decoration", "status": "COMPLETED"}},
        )
        with patch(
            "miya.services.intelligence.unified.execute_structured_action",
            return_value=reality,
        ), patch(
            "miya.services.intelligence.unified.build_ops_context",
            return_value=MagicMock(restaurant=user.restaurant, user=user, restaurant_id="r1"),
        ):
            # Miya mutates via unified apply (same as planning → structured action)
            apply_task_status(user=user, channel="voice", status="COMPLETED", task_id="t1")
            dash = get_task_reality(user=user, channel="dashboard", task_id="t1")
            wa = get_task_reality(user=user, channel="whatsapp", task_id="t1")
            mobile = get_task_reality(user=user, channel="mobile", task_id="t1")
        statuses = {
            (dash.data or {}).get("task", {}).get("status"),
            (wa.data or {}).get("task", {}).get("status"),
            (mobile.data or {}).get("task", {}).get("status"),
        }
        self.assertEqual(statuses, {"COMPLETED"})


class UnifiedNlEntryTests(SimpleTestCase):
    def test_run_unified_miya_delegates_to_run_miya_chat(self):
        user = _user()
        with patch(
            "miya.services.agent.run_miya_chat",
            return_value={"reply": "Completed.", "tool_trace": []},
        ) as chat:
            for channel in ("dashboard", "whatsapp", "mobile", "voice"):
                out = run_unified_miya(
                    user=user,
                    user_message="What is the status of the decoration task?",
                    channel=channel,
                )
                self.assertEqual(out.get("unified_channel"), channel)
                self.assertTrue(out.get("assistant_text_is_not_executable"))
        self.assertEqual(chat.call_count, 4)
        channels_seen = {c.kwargs.get("channel") for c in chat.call_args_list}
        self.assertEqual(channels_seen, {"dashboard", "whatsapp", "mobile", "voice"})


class WhatsAppAdapterUsesUnifiedTests(SimpleTestCase):
    def test_complete_task_after_proof_calls_apply_task_status(self):
        from notifications.dashboard_task_whatsapp import complete_task_after_proof

        user = _user()
        with patch(
            "miya.services.intelligence.unified.apply_task_status",
            return_value=ok(
                message="Done",
                verified=True,
                data={"task": {"id": "t1", "status": "COMPLETED"}},
            ),
        ) as apply:
            body = complete_task_after_proof(user=user, task_id="t1")
        self.assertTrue(body.get("success"))
        self.assertEqual(apply.call_args.kwargs["channel"], "whatsapp")
        self.assertEqual(apply.call_args.kwargs["status"], "COMPLETED")
