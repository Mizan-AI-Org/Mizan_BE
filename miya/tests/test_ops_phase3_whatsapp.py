"""Phase 3: WhatsApp converges on the same ops services as Miya/dashboard."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from notifications.dashboard_task_whatsapp import (
    _extract_task_query,
    _normalize_status_intent,
    looks_like_dashboard_task_status_reply,
)
from miya.services.ops.result import fail, ok


class WhatsAppTaskIntentTests(SimpleTestCase):
    def test_natural_complete_phrase(self):
        text = "I completed my closing checklist."
        self.assertTrue(looks_like_dashboard_task_status_reply(text))
        self.assertEqual(_normalize_status_intent(text), "COMPLETED")
        self.assertIn("closing checklist", _extract_task_query(text).lower())

    def test_keyword_done(self):
        self.assertTrue(looks_like_dashboard_task_status_reply("done"))
        self.assertEqual(_normalize_status_intent("done #7ffc0d68"), "COMPLETED")

    def test_checklist_start_not_hijacked(self):
        self.assertFalse(looks_like_dashboard_task_status_reply("start my checklist"))


class WhatsAppUsesCanonicalOpsTests(SimpleTestCase):
    def test_handle_done_calls_update_task_status(self):
        from notifications.dashboard_task_whatsapp import handle_dashboard_task_whatsapp_reply

        user = MagicMock()
        user.id = "u1"
        user.role = "WAITER"
        user.restaurant = MagicMock(id="r1")
        user.restaurant_id = "r1"
        user.first_name = "Ahmed"
        user.last_name = "H"
        user.email = "a@ex.com"
        user.language = "en"

        ns = MagicMock()
        ctx = MagicMock()
        ctx.restaurant = user.restaurant
        ctx.user = user
        ctx.user_id = "u1"
        ctx.channel = "whatsapp"

        find_result = ok(
            message="Found 1",
            verified=True,
            data={
                "tasks": [
                    {
                        "id": "t1",
                        "task_ref": "#ABCDEF12",
                        "title": "Closing checklist",
                        "status": "PENDING",
                    }
                ]
            },
        )
        update_result = ok(
            message="Updated",
            verified=True,
            data={
                "task": {
                    "id": "t1",
                    "title": "Closing checklist",
                    "status": "COMPLETED",
                    "task_ref": "#ABCDEF12",
                },
                "previous_status": "PENDING",
            },
        )

        with patch(
            "notifications.dashboard_task_whatsapp._ops_ctx", return_value=ctx
        ), patch(
            "miya.services.ops.tasks.find_tasks", return_value=find_result
        ) as mock_find, patch(
            "miya.services.ops.tasks.update_task_status", return_value=update_result
        ) as mock_upd, patch(
            "dashboard.models.Task"
        ) as TaskModel:
            TaskModel.objects.filter.return_value.first.return_value = MagicMock(
                id="t1",
                title="Closing checklist",
                require_photo_proof=False,
                proof_media_url=None,
                restaurant=user.restaurant,
            )
            handled = handle_dashboard_task_whatsapp_reply(
                notification_service=ns,
                user=user,
                phone_digits="212600000000",
                text_body="I completed my closing checklist.",
                session=None,
            )

        self.assertTrue(handled)
        mock_upd.assert_called_once()
        kwargs = mock_upd.call_args.kwargs
        self.assertEqual(kwargs.get("status"), "COMPLETED")
        self.assertTrue(kwargs.get("assignee_scope"))
        ns.send_whatsapp_text.assert_called()
        reply = ns.send_whatsapp_text.call_args[0][1]
        self.assertIn("Closing checklist", reply)
        self.assertNotIn("I couldn't", reply)

    def test_failed_update_does_not_claim_done(self):
        from notifications.dashboard_task_whatsapp import handle_dashboard_task_whatsapp_reply

        user = MagicMock()
        user.id = "u1"
        user.role = "WAITER"
        user.restaurant = MagicMock(id="r1")
        user.restaurant_id = "r1"
        ns = MagicMock()
        ctx = MagicMock()
        ctx.restaurant = user.restaurant
        ctx.user = user
        ctx.user_id = "u1"

        with patch(
            "notifications.dashboard_task_whatsapp._ops_ctx", return_value=ctx
        ), patch(
            "miya.services.ops.tasks.find_tasks",
            return_value=fail(code="task_not_found", message="No matching tasks found.", data={"tasks": []}),
        ), patch(
            "miya.services.ops.tasks.update_task_status",
            return_value=fail(code="task_not_found", message="I couldn't find that task."),
        ):
            handled = handle_dashboard_task_whatsapp_reply(
                notification_service=ns,
                user=user,
                phone_digits="212600000000",
                text_body="done",
                session=None,
            )
        self.assertTrue(handled)
        reply = ns.send_whatsapp_text.call_args[0][1].lower()
        self.assertTrue("couldn't" in reply or "could not" in reply or "list" in reply)


class CanonicalConvergenceContractTests(SimpleTestCase):
    """Same service entrypoints for Dashboard/Miya/WhatsApp."""

    def test_whatsapp_incident_create_uses_ops(self):
        import inspect
        from notifications.views import _create_safety_concern_from_whatsapp

        src = inspect.getsource(_create_safety_concern_from_whatsapp)
        self.assertIn("create_incident", src)
        self.assertNotIn("SafetyConcernReport.objects.create", src)

    def test_whatsapp_task_handler_uses_ops(self):
        import inspect
        from notifications import dashboard_task_whatsapp as mod

        src = inspect.getsource(mod.handle_dashboard_task_whatsapp_reply)
        self.assertIn("update_task_status", src)
        self.assertNotIn("task.status = intent", src)

    def test_report_incident_is_canonical(self):
        from miya.services.ops import CANONICAL_TOOL_NAMES

        self.assertIn("report_incident", CANONICAL_TOOL_NAMES)
        self.assertIn("confirm_meeting", CANONICAL_TOOL_NAMES)
        self.assertIn("route_incident", CANONICAL_TOOL_NAMES)


class AssignViaOpsSameAsMiyaTests(SimpleTestCase):
    @patch("miya.services.ops.tasks.assign_task")
    def test_dispatch_assign_is_shared(self, mock_assign):
        from miya.services.ops import dispatch_canonical_tool
        from miya.services.ops.context import OpsContext

        mock_assign.return_value = ok(message="Assigned", verified=True, data={"task": {"id": "t1"}})
        ctx = OpsContext(
            user=MagicMock(id="m1", role="MANAGER"),
            restaurant=MagicMock(id="r1"),
            restaurant_id="r1",
            user_id="m1",
            role="MANAGER",
            channel="whatsapp",
        )
        result = dispatch_canonical_tool(
            "reassign_dashboard_task",
            {"q": "closing checklist", "assignee_name": "Ahmed"},
            ctx=ctx,
        )
        self.assertTrue(result.success)
        mock_assign.assert_called_once()
