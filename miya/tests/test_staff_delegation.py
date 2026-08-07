"""Tests for manager→single-staff delegation routing."""

from __future__ import annotations

import json
from unittest.mock import patch

from django.test import TestCase

from miya.services.staff_delegation import (
    audience_is_broadcast,
    parse_staff_delegation,
)


class StaffDelegationParseTests(TestCase):
    def test_parse_tell_name_to_action(self):
        parsed = parse_staff_delegation("tell adama to prepare the buffet")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["staff_name"].lower(), "adama")
        self.assertIn("buffet", parsed["task_title"].lower())

    def test_parse_ask_without_to(self):
        parsed = parse_staff_delegation("ask John clean the bar")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["staff_name"], "John")

    def test_broadcast_not_parsed_as_single(self):
        self.assertIsNone(parse_staff_delegation("tell the team dinner is late"))
        self.assertIsNone(parse_staff_delegation("tell everyone we're closed"))

    def test_hr_delegation_not_single_staff(self):
        self.assertIsNone(parse_staff_delegation("tell HR to pay all staff"))

    def test_audience_all_is_broadcast(self):
        self.assertTrue(audience_is_broadcast("all"))
        self.assertFalse(audience_is_broadcast({"staff_ids": ["abc"]}))


class SendAnnouncementGuardTests(TestCase):
    @patch("notifications.services.NotificationService.send_custom_notification", return_value=(True, {}))
    def test_requires_explicit_broadcast_without_filters(self, _mock_send):
        from notifications.services import NotificationService

        service = NotificationService()
        ok, count, err, _details = service.send_announcement_to_audience(
            restaurant_id="00000000-0000-0000-0000-000000000001",
            title="Hi",
            message="Hello",
            broadcast_all=False,
        )
        self.assertFalse(ok)
        self.assertEqual(count, 0)
        self.assertIn("Specify who should receive", err or "")

    def test_agent_endpoint_rejects_missing_audience(self):
        from rest_framework.test import APIClient

        from accounts.models import CustomUser, Restaurant
        from core.agent_auth import primary_agent_bearer_token

        restaurant = Restaurant.objects.create(name="Ann Resto", slug="ann-resto")
        CustomUser.objects.create_user(
            email="mgr-ann@test.com",
            password="pass",
            role="MANAGER",
            restaurant=restaurant,
        )
        client = APIClient()
        agent_key = primary_agent_bearer_token()
        if not agent_key:
            self.skipTest("Agent key not configured")
        response = client.post(
            "/api/notifications/agent/announcement/",
            data=json.dumps(
                {
                    "restaurant_id": str(restaurant.id),
                    "message": "Kitchen closes early",
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {agent_key}",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("Specify who should receive", response.json().get("error", ""))
