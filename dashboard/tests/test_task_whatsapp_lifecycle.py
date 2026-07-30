"""Dashboard.Task WhatsApp lifecycle + agent status/reassign."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import CustomUser, Restaurant
from dashboard.models import Task
from notifications.dashboard_task_whatsapp import (
    handle_dashboard_task_whatsapp_reply,
    looks_like_dashboard_task_status_reply,
    _normalize_status_intent,
)


class DashboardTaskWhatsAppHelpersTests(TestCase):
    def test_looks_like_status_reply(self):
        self.assertTrue(looks_like_dashboard_task_status_reply("done"))
        self.assertTrue(looks_like_dashboard_task_status_reply("accept"))
        self.assertTrue(looks_like_dashboard_task_status_reply("unable"))
        self.assertTrue(looks_like_dashboard_task_status_reply("my tasks"))
        self.assertFalse(looks_like_dashboard_task_status_reply("hello there"))

    def test_normalize_intents(self):
        self.assertEqual(_normalize_status_intent("done"), "COMPLETED")
        self.assertEqual(_normalize_status_intent("accept"), "ACCEPTED")
        self.assertEqual(_normalize_status_intent("start"), "IN_PROGRESS")
        self.assertEqual(_normalize_status_intent("unable to complete"), "UNABLE_TO_COMPLETE")
        self.assertEqual(_normalize_status_intent("tasks"), "LIST")


class DashboardTaskWhatsAppHandlerTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="WA Task Cafe")
        self.staff = CustomUser.objects.create_user(
            email="staff@wa.com",
            password="x",
            first_name="Sam",
            last_name="Staff",
            role="STAFF",
            restaurant=self.restaurant,
            phone="+212611111111",
        )
        self.task = Task.objects.create(
            restaurant=self.restaurant,
            assigned_to=self.staff,
            title="Clean fryer",
            status="PENDING",
            priority="HIGH",
        )
        self.ns = MagicMock()
        self.ns.send_whatsapp_text.return_value = (True, {})
        self.ns.send_custom_notification.return_value = None

    def test_done_marks_completed(self):
        ok = handle_dashboard_task_whatsapp_reply(
            notification_service=self.ns,
            user=self.staff,
            phone_digits="212611111111",
            text_body="done",
        )
        self.assertTrue(ok)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "COMPLETED")
        self.assertIsNotNone(self.task.completed_at)

    def test_accept_then_unable(self):
        handle_dashboard_task_whatsapp_reply(
            notification_service=self.ns,
            user=self.staff,
            phone_digits="212611111111",
            text_body="accept",
        )
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "ACCEPTED")

        handle_dashboard_task_whatsapp_reply(
            notification_service=self.ns,
            user=self.staff,
            phone_digits="212611111111",
            text_body="unable",
        )
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "UNABLE_TO_COMPLETE")


@override_settings(LUA_WEBHOOK_API_KEY="test-agent-key")
class AgentTaskStatusReassignTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Agent Task Bistro")
        self.manager = CustomUser.objects.create_user(
            email="mgr@agent.com",
            password="x",
            first_name="Mia",
            last_name="Mgr",
            role="MANAGER",
            restaurant=self.restaurant,
            phone="+212622222222",
        )
        self.staff = CustomUser.objects.create_user(
            email="cook@agent.com",
            password="x",
            first_name="Carl",
            last_name="Cook",
            role="STAFF",
            restaurant=self.restaurant,
            phone="+212633333333",
        )
        self.other = CustomUser.objects.create_user(
            email="other@agent.com",
            password="x",
            first_name="Ola",
            last_name="Other",
            role="STAFF",
            restaurant=self.restaurant,
            phone="+212644444444",
        )
        self.task = Task.objects.create(
            restaurant=self.restaurant,
            assigned_to=self.staff,
            title="Prep mise en place",
            status="PENDING",
            priority="MEDIUM",
        )
        self.client = APIClient()

    def _auth_headers(self):
        return {
            "HTTP_AUTHORIZATION": "Bearer test-agent-key",
            "HTTP_X_RESTAURANT_ID": str(self.restaurant.id),
        }

    @patch("notifications.services.notification_service.send_whatsapp_text", return_value=(True, {}))
    @patch("notifications.services.notification_service.send_custom_notification")
    def test_agent_status_completed(self, _notif, _wa):
        resp = self.client.post(
            "/api/dashboard/agent/tasks/status/",
            data={
                "restaurant_id": str(self.restaurant.id),
                "task_id": str(self.task.id),
                "status": "COMPLETED",
                "user_id": str(self.staff.id),
            },
            format="json",
            **self._auth_headers(),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        self.assertTrue(resp.data.get("success"))
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "COMPLETED")

    @patch("notifications.services.notification_service.send_whatsapp_text", return_value=(True, {}))
    @patch("notifications.services.notification_service.send_custom_notification")
    def test_agent_reassign(self, _notif, _wa):
        resp = self.client.post(
            "/api/dashboard/agent/tasks/reassign/",
            data={
                "restaurant_id": str(self.restaurant.id),
                "task_id": str(self.task.id),
                "user_id": str(self.other.id),
                "notify_whatsapp": True,
            },
            format="json",
            **self._auth_headers(),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        self.assertTrue(resp.data.get("success"), resp.data)
        self.task.refresh_from_db()
        self.assertEqual(self.task.assigned_to_id, self.other.id)

    def test_widget_status_accepts_new_statuses(self):
        self.client.force_authenticate(self.manager)
        resp = self.client.patch(
            f"/api/dashboard/tasks-demands/{self.task.id}/status/",
            data={"status": "ACCEPTED"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "ACCEPTED")
