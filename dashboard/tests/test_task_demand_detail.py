"""Tests for GET /api/dashboard/tasks-demands/<uuid>/."""

from __future__ import annotations

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import CustomUser, Restaurant
from dashboard.models import Task


class TaskDemandDetailViewTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Travel Bistro")
        self.user = CustomUser.objects.create_user(
            email="manager@travel.test",
            password="testpass123",
            restaurant=self.restaurant,
            role="MANAGER",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.task = Task.objects.create(
            restaurant=self.restaurant,
            title="Meeting request with team",
            description="Schedule offsite travel",
            category="SCHEDULING",
            status="IN_PROGRESS",
            priority="MEDIUM",
            source="MIYA",
        )

    def test_get_dashboard_task_by_id(self):
        resp = self.client.get(f"/api/dashboard/tasks-demands/{self.task.id}/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["id"], str(self.task.id))
        self.assertEqual(resp.data["kind"], "dashboard")
        self.assertEqual(resp.data["title"], "Meeting request with team")
        self.assertEqual(resp.data["status"], "IN_PROGRESS")

    def test_get_unknown_id_returns_404(self):
        resp = self.client.get(
            "/api/dashboard/tasks-demands/00000000-0000-0000-0000-000000000099/"
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
