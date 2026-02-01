"""
Tests for scheduling agent endpoints (Miya/Lua).
Verifies restaurant context resolution via JWT, API key + X-Restaurant-Id, and body.

Run with: python manage.py test scheduling.tests.test_agent_views (with venv activated).
"""
from unittest.mock import patch
from rest_framework.test import APITestCase, APIClient
from rest_framework import status

from accounts.models import CustomUser, Restaurant
from scheduling.models import WeeklySchedule, AssignedShift


# Agent key used in tests (must match what we patch in settings)
TEST_AGENT_KEY = "test-agent-key-miya"


@patch("scheduling.views_agent.settings")
class SchedulingAgentViewsTests(APITestCase):
    """Test agent endpoints resolve restaurant and never return raw 'Unable to resolve restaurant context' when context is provided."""

    def setUp(self):
        self.client = APIClient()
        self.restaurant = Restaurant.objects.create(
            name="Test Restaurant",
            email="restaurant@test.local",
        )
        self.staff = CustomUser.objects.create_user(
            email="staff@test.local",
            password="StaffPass123!",
            role="WAITER",
            restaurant=self.restaurant,
            first_name="Test",
            last_name="Staff",
        )
        self.admin = CustomUser.objects.create_user(
            email="admin@test.local",
            password="AdminPass123!",
            role="ADMIN",
            restaurant=self.restaurant,
            first_name="Admin",
            last_name="User",
        )

    def _agent_headers(self, use_restaurant_header=True):
        headers = {
            "Authorization": f"Bearer {TEST_AGENT_KEY}",
            "Content-Type": "application/json",
        }
        if use_restaurant_header:
            headers["X-Restaurant-Id"] = str(self.restaurant.id)
        return headers

    def test_agent_list_staff_with_x_restaurant_id(self, mock_settings):
        mock_settings.LUA_WEBHOOK_API_KEY = TEST_AGENT_KEY
        url = "/api/scheduling/agent/staff/"
        resp = self.client.get(url, headers=self._agent_headers())
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        data = resp.json()
        self.assertIsInstance(data, list)
        self.assertGreaterEqual(len(data), 1)
        emails = [s.get("email") for s in data]
        self.assertIn("staff@test.local", emails)

    @patch("scheduling.views_agent.settings")
    def test_agent_list_staff_rejects_unable_to_resolve_when_header_sent(self, mock_settings):
        """When X-Restaurant-Id is sent, we must not return 'Unable to resolve restaurant context'."""
        mock_settings.LUA_WEBHOOK_API_KEY = TEST_AGENT_KEY
        url = "/api/scheduling/agent/staff/"
        resp = self.client.get(url, headers=self._agent_headers())
        self.assertNotIn("Unable to resolve restaurant context", resp.json() if isinstance(resp.json(), str) else str(resp.json()))

    @patch("scheduling.views_agent.settings")
    def test_agent_staff_count_with_x_restaurant_id(self, mock_settings):
        mock_settings.LUA_WEBHOOK_API_KEY = TEST_AGENT_KEY
        url = "/api/scheduling/agent/staff-count/"
        resp = self.client.get(url, headers=self._agent_headers())
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        data = resp.json()
        self.assertIn("count", data)
        self.assertIn("message", data)
        self.assertGreaterEqual(data["count"], 1)

    @patch("scheduling.views_agent.settings")
    def test_agent_create_shift_with_x_restaurant_id(self, mock_settings):
        mock_settings.LUA_WEBHOOK_API_KEY = TEST_AGENT_KEY
        url = "/api/scheduling/agent/create-shift/"
        payload = {
            "restaurant_id": str(self.restaurant.id),
            "staff_id": str(self.staff.id),
            "shift_date": "2026-02-09",
            "start_time": "09:00",
            "end_time": "17:00",
        }
        resp = self.client.post(url, payload, format="json", headers=self._agent_headers())
        # 201 created or 409 conflict (e.g. duplicate) is success; 400 with "Unable to resolve" would be wrong
        self.assertIn(resp.status_code, (status.HTTP_201_CREATED, status.HTTP_409_CONFLICT), resp.content)
        if resp.status_code == 400:
            err = resp.json().get("error", "")
            self.assertNotIn("Unable to resolve restaurant context", err, "Must not fail on context when X-Restaurant-Id is sent")

    @patch("scheduling.views_agent.settings")
    def test_agent_optimize_schedule_with_x_restaurant_id(self, mock_settings):
        mock_settings.LUA_WEBHOOK_API_KEY = TEST_AGENT_KEY
        url = "/api/scheduling/agent/optimize-schedule/"
        payload = {
            "restaurant_id": str(self.restaurant.id),
            "week_start": "2026-02-02",
            "department": "all",
        }
        resp = self.client.post(url, payload, format="json", headers=self._agent_headers())
        # 200 with result or 400 for business logic (e.g. no demand data) is ok; 400 "Unable to resolve" is not
        self.assertIn(resp.status_code, (status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST), resp.content)
        if resp.status_code == 400:
            err = resp.json().get("error", "")
            self.assertNotIn("Unable to resolve restaurant context", err, "Must not fail on context when X-Restaurant-Id is sent")

    @patch("scheduling.views_agent.settings")
    def test_agent_staff_count_with_body_restaurant_id(self, mock_settings):
        """Restaurant ID in query/body (no header) should still resolve."""
        mock_settings.LUA_WEBHOOK_API_KEY = TEST_AGENT_KEY
        url = "/api/scheduling/agent/staff-count/"
        resp = self.client.get(
            url,
            data={"restaurant_id": str(self.restaurant.id)},
            headers={"Authorization": f"Bearer {TEST_AGENT_KEY}", "Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        data = resp.json()
        self.assertIn("count", data)
