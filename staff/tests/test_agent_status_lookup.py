"""Tests for incident/request status lookup via Miya agent endpoints."""

from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory

from accounts.models import CustomUser, Restaurant
from accounts.rbac_enforce import allowed_tools_for_user
from staff.models_task import SafetyConcernReport
from staff.views_agent import agent_list_incidents, agent_search_operational_records


@override_settings(MIYA_MASTRA_API_KEY="test-agent-key")
class AgentStatusLookupTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.restaurant = Restaurant.objects.create(name="Test Bistro", slug="test-bistro-status")
        self.staff = CustomUser.objects.create_user(
            email="staff@test.com",
            password="pass12345",
            restaurant=self.restaurant,
            role="STAFF",
        )
        self.manager = CustomUser.objects.create_user(
            email="mgr@test.com",
            password="pass12345",
            restaurant=self.restaurant,
            role="MANAGER",
        )
        self.open_incident = SafetyConcernReport.objects.create(
            restaurant=self.restaurant,
            title="Computer screen broken",
            description="Laptop display cracked — needs repair",
            reporter=self.staff,
            status="OPEN",
        )
        self.resolved_incident = SafetyConcernReport.objects.create(
            restaurant=self.restaurant,
            title="Fridge repair",
            description="Walk-in fridge fixed",
            reporter=self.staff,
            status="RESOLVED",
        )

    def _list_incidents(self, q: str, *, user=None, status: str | None = None):
        params = {"restaurant_id": str(self.restaurant.id), "q": q}
        if status is not None:
            params["status"] = status
        request = self.factory.get(
            "/api/staff/agent/incidents/",
            params,
            HTTP_AUTHORIZATION="Bearer test-agent-key",
        )
        if user:
            request.META["HTTP_X_ACTING_USER_ID"] = str(user.id)
        return agent_list_incidents(request)

    def _search(self, q: str, *, user=None):
        request = self.factory.get(
            "/api/staff/agent/records/search/",
            {"restaurant_id": str(self.restaurant.id), "q": q},
            HTTP_AUTHORIZATION="Bearer test-agent-key",
        )
        if user:
            request.META["HTTP_X_ACTING_USER_ID"] = str(user.id)
        return agent_search_operational_records(request)

    def test_list_incidents_with_q_searches_all_statuses(self):
        response = self._list_incidents("fridge")
        self.assertEqual(response.status_code, 200)
        data = response.data
        self.assertTrue(data["success"])
        titles = {i["title"] for i in data["incidents"]}
        self.assertIn("Fridge repair", titles)
        self.assertIn("resolved", data["message_for_user"].lower())

    def test_search_finds_incident_by_question_keywords(self):
        response = self._search("Has the computer screen been repaired")
        self.assertEqual(response.status_code, 200)
        data = response.data
        self.assertTrue(data["success"])
        self.assertGreaterEqual(data["count"], 1)
        types = {m["type"] for m in data["matches"]}
        self.assertIn("incident", types)
        self.assertIn("open", data["message_for_user"].lower())

    def test_staff_can_use_status_lookup_tools(self):
        allowed = allowed_tools_for_user(self.staff, self.restaurant)
        self.assertIn("list_incidents", allowed)
        self.assertIn("search_operational_records", allowed)
        self.assertNotIn("close_incident", allowed)
