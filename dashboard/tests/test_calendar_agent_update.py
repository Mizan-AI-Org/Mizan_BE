"""Tests for Miya calendar list/update agent endpoints."""

from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory

from accounts.models import CustomUser, Restaurant
from dashboard.api.calendar_write import (
    _event_matches_query,
    _format_calendar_search_reply,
    agent_list_calendar_events,
    agent_update_calendar_event,
    agent_delete_calendar_event,
)


@override_settings(MIYA_MASTRA_API_KEY="test-agent-key")
class CalendarAgentUpdateTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.restaurant = Restaurant.objects.create(name="Test Bistro", slug="test-cal-bistro")
        self.user = CustomUser.objects.create_user(
            email="mgr@test.com",
            password="pass12345",
            restaurant=self.restaurant,
            role="MANAGER",
        )

    def test_event_matches_query_by_person_name(self):
        row = {
            "title": "Rendez-vous avec Loubna Beldi Country Club",
            "location": "Beldi Country Club",
        }
        self.assertTrue(
            _event_matches_query(row, q_lower="loubna", tokens=["loubna"])
        )

    def test_format_search_reply_single_match(self):
        msg = _format_calendar_search_reply(
            [{"title": "Rendez-vous avec Loubna", "start": "2026-08-06T09:00:00", "location": "Zama"}],
            q="Loubna",
        )
        self.assertIn("Loubna", msg)
        self.assertIn("update_calendar_event", msg)
        self.assertIn("delete_calendar_event", msg)

    @patch("dashboard.api.calendar_write._search_calendar_events")
    def test_list_calendar_events_returns_matches(self, mock_search):
        mock_search.return_value = (
            [
                {
                    "id": "evt-123",
                    "title": "Rendez-vous avec Loubna",
                    "start": "2026-08-06T09:00:00",
                    "location": "Beldi",
                }
            ],
            None,
        )
        request = self.factory.get(
            "/api/dashboard/agent/calendar-events/list/",
            {"restaurant_id": str(self.restaurant.id), "q": "Loubna"},
            HTTP_AUTHORIZATION="Bearer test-agent-key",
        )
        response = agent_list_calendar_events(request)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["events"][0]["id"], "evt-123")

    @patch("dashboard.api.calendar_write._update_single_calendar_event")
    def test_update_calendar_event_endpoint(self, mock_update):
        mock_update.return_value = {
            "success": True,
            "event_id": "evt-123",
            "message_for_user": 'Updated "Rendez-vous avec Loubna" — Zama.',
        }
        request = self.factory.post(
            "/api/dashboard/agent/calendar-events/update/",
            {
                "restaurant_id": str(self.restaurant.id),
                "event_id": "evt-123",
                "location": "Zama",
            },
            format="json",
            HTTP_AUTHORIZATION="Bearer test-agent-key",
        )
        response = agent_update_calendar_event(request)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        mock_update.assert_called_once()

    @patch("dashboard.api.calendar_write._delete_single_calendar_event")
    def test_delete_calendar_event_endpoint(self, mock_delete):
        mock_delete.return_value = {
            "success": True,
            "event_id": "evt-123",
            "message_for_user": '🗑️ Removed "Rendez-vous avec Loubna" from your calendar.',
        }
        request = self.factory.post(
            "/api/dashboard/agent/calendar-events/delete/",
            {
                "restaurant_id": str(self.restaurant.id),
                "event_id": "evt-123",
            },
            format="json",
            HTTP_AUTHORIZATION="Bearer test-agent-key",
        )
        response = agent_delete_calendar_event(request)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        mock_delete.assert_called_once()
