"""Tests for agent_search_operational_records."""

from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory

from accounts.models import CustomUser, Restaurant
from dashboard.models import Task
from staff.models import StaffRequest
from staff.views_agent import agent_search_operational_records


@override_settings(LUA_WEBHOOK_API_KEY="test-agent-key")
class AgentSearchOperationalRecordsTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.restaurant = Restaurant.objects.create(name="Test Bistro", slug="test-bistro")
        self.user = CustomUser.objects.create_user(
            email="mgr@test.com",
            password="pass12345",
            restaurant=self.restaurant,
        )
        self.req = StaffRequest.objects.create(
            restaurant=self.restaurant,
            subject="Follow up with Lucille Kremer — artists budget",
            description="Personal ops reminder",
            category="OPERATIONS",
            staff=self.user,
        )
        self.task = Task.objects.create(
            restaurant=self.restaurant,
            assigned_to=self.user,
            title="Follow up with Lucille Kremer — artists budget",
            category="OPERATIONS",
        )

    def _search(self, q: str):
        request = self.factory.get(
            "/api/staff/agent/records/search/",
            {"restaurant_id": str(self.restaurant.id), "q": q},
            HTTP_AUTHORIZATION="Bearer test-agent-key",
        )
        return agent_search_operational_records(request)

    def test_finds_staff_request_by_ref_tail(self):
        ref = str(self.req.id).replace("-", "")[-8:].upper()
        response = self._search(ref)
        self.assertEqual(response.status_code, 200)
        data = response.data
        self.assertTrue(data["success"])
        self.assertGreaterEqual(data["count"], 1)
        types = {m["type"] for m in data["matches"]}
        self.assertIn("staff_request", types)

    def test_finds_task_by_subject_keyword(self):
        response = self._search("Lucille Kremer")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        self.assertGreaterEqual(response.data["count"], 1)
