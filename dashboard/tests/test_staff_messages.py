"""Tests for dashboard Staff Messages widget API."""

from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models import CustomUser, Restaurant, StaffProfile
from dashboard.api.staff_messages import StaffMessagesSendView


@override_settings(LUA_WEBHOOK_API_KEY="test-agent-key")
class StaffMessagesSendViewTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.restaurant = Restaurant.objects.create(name="Msg Bistro", slug="msg-bistro")
        self.manager = CustomUser.objects.create_user(
            email="mgr@msg.com",
            password="pass12345",
            restaurant=self.restaurant,
            role="MANAGER",
            first_name="Manager",
            phone="+212600000001",
        )
        self.chef = CustomUser.objects.create_user(
            email="chef@msg.com",
            password="pass12345",
            restaurant=self.restaurant,
            role="CHEF",
            first_name="Chef",
            phone="+212600000002",
        )
        StaffProfile.objects.get_or_create(user=self.chef)
        self.waiter = CustomUser.objects.create_user(
            email="waiter@msg.com",
            password="pass12345",
            restaurant=self.restaurant,
            role="WAITER",
            first_name="Waiter",
            phone="+212600000003",
        )
        StaffProfile.objects.get_or_create(user=self.waiter)

    def _post(self, payload, user=None):
        request = self.factory.post(
            "/api/dashboard/staff-messages/send/",
            payload,
            format="json",
        )
        force_authenticate(request, user=user or self.manager)
        return StaffMessagesSendView.as_view()(request)

    @patch("dashboard.api.staff_messages.NotificationService.send_announcement_to_audience")
    def test_send_to_single_recipient(self, mock_send):
        mock_send.return_value = (True, 1, None, {"whatsapp_sent": 1})

        response = self._post(
            {
                "body": "Shift starts in 30 minutes",
                "recipient_user_id": str(self.chef.id),
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        self.assertEqual(kwargs["staff_ids"], [str(self.chef.id)])
        self.assertEqual(kwargs["channels"], ["app", "whatsapp"])

    @patch("dashboard.api.staff_messages.NotificationService.send_announcement_to_audience")
    def test_send_to_role_audience(self, mock_send):
        mock_send.return_value = (True, 2, None, {"whatsapp_sent": 2})

        response = self._post(
            {
                "body": "Kitchen meeting at 3pm",
                "roles": ["CHEF", "KITCHEN_STAFF"],
            }
        )
        self.assertEqual(response.status_code, 200)
        kwargs = mock_send.call_args.kwargs
        self.assertEqual(kwargs["roles"], ["CHEF", "KITCHEN_STAFF"])
        self.assertIsNone(kwargs["staff_ids"])

    @patch("dashboard.api.staff_messages.NotificationService.send_announcement_to_audience")
    def test_send_to_department_audience(self, mock_send):
        mock_send.return_value = (True, 1, None, {"whatsapp_sent": 1})
        profile = self.chef.profile
        profile.department = "Kitchen"
        profile.save(update_fields=["department"])

        response = self._post(
            {
                "body": "Deep clean tonight",
                "departments": ["Kitchen"],
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_send.call_args.kwargs["departments"], ["Kitchen"])

    @patch("dashboard.api.staff_messages.NotificationService.send_announcement_to_audience")
    def test_no_recipients_returns_friendly_error(self, mock_send):
        mock_send.return_value = (False, 0, "No recipients found for the given audience", {})

        response = self._post({"body": "Hello team", "roles": ["CHEF"]})
        self.assertEqual(response.status_code, 400)
        self.assertIn("CHEF", response.data["error"])

    def test_waiter_cannot_send(self):
        response = self._post(
            {"body": "Hi", "recipient_user_id": str(self.chef.id)},
            user=self.waiter,
        )
        self.assertEqual(response.status_code, 403)

    def test_requires_body(self):
        response = self._post({"recipient_user_id": str(self.chef.id)})
        self.assertEqual(response.status_code, 400)
        self.assertIn("body", response.data["error"])
