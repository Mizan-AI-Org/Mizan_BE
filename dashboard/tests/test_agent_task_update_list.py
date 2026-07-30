"""Agent task update / overdue list endpoints."""

from __future__ import annotations

from datetime import date, timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import CustomUser, Restaurant
from dashboard.models import Task


@override_settings(LUA_WEBHOOK_API_KEY="test-agent-key")
class AgentTaskUpdateListTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.restaurant = Restaurant.objects.create(name="Ops Cafe")
        self.manager = CustomUser.objects.create_user(
            email="mgr@ops.com",
            password="pass12345",
            restaurant=self.restaurant,
            role="MANAGER",
            first_name="Maya",
            last_name="Mgr",
        )
        self.staff = CustomUser.objects.create_user(
            email="staff@ops.com",
            password="pass12345",
            restaurant=self.restaurant,
            role="STAFF",
            first_name="Ahmed",
            last_name="Cook",
        )
        self.headers = {"HTTP_AUTHORIZATION": "Bearer test-agent-key"}
        self.task = Task.objects.create(
            restaurant=self.restaurant,
            title="Kitchen cleaning",
            description="Deep clean",
            status="PENDING",
            priority="MEDIUM",
            assigned_to=self.staff,
            created_by=self.manager,
            due_date=date.today() - timedelta(days=1),
        )

    def test_update_priority_and_due(self):
        resp = self.client.post(
            "/api/dashboard/agent/tasks/update/",
            {
                "restaurant_id": str(self.restaurant.id),
                "task_id": str(self.task.id),
                "priority": "HIGH",
                "due_date": (date.today() + timedelta(days=2)).isoformat(),
            },
            format="json",
            **self.headers,
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        self.assertTrue(resp.data.get("success"))
        self.task.refresh_from_db()
        self.assertEqual(self.task.priority, "HIGH")
        self.assertEqual(self.task.due_date, date.today() + timedelta(days=2))

    def test_list_overdue(self):
        Task.objects.create(
            restaurant=self.restaurant,
            title="Fresh task",
            status="PENDING",
            priority="LOW",
            assigned_to=self.staff,
            created_by=self.manager,
            due_date=date.today() + timedelta(days=3),
        )
        resp = self.client.post(
            "/api/dashboard/agent/tasks/list/",
            {
                "restaurant_id": str(self.restaurant.id),
                "overdue": True,
            },
            format="json",
            **self.headers,
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        self.assertTrue(resp.data.get("success"))
        titles = [t["title"] for t in resp.data.get("tasks") or []]
        self.assertIn("Kitchen cleaning", titles)
        self.assertNotIn("Fresh task", titles)
