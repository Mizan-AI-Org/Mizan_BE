from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from accounts.models import CustomUser, Restaurant
from staff.follow_up_helpers import should_send_follow_up
from staff.models import StaffRequest
from staff.tasks import staff_request_follow_up_sweep


class FollowUpHelperTests(TestCase):
    def test_should_send_follow_up_respects_schedule(self):
        notified = timezone.now() - timedelta(hours=5)
        self.assertTrue(
            should_send_follow_up(
                notified_at=notified,
                priority="MEDIUM",
                follow_up_count=0,
                follow_up_max=2,
                last_follow_up_at=None,
            )
        )
        self.assertFalse(
            should_send_follow_up(
                notified_at=timezone.now() - timedelta(hours=1),
                priority="MEDIUM",
                follow_up_count=0,
                follow_up_max=2,
                last_follow_up_at=None,
            )
        )


class StaffRequestFollowUpSweepTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Test Resto", slug="test-resto")
        self.assignee = CustomUser.objects.create_user(
            email="assignee@test.com",
            password="pass12345",
            restaurant=self.restaurant,
            role="MANAGER",
            phone="+212600000001",
        )
        self.manager = CustomUser.objects.create_user(
            email="manager@test.com",
            password="pass12345",
            restaurant=self.restaurant,
            role="OWNER",
            phone="+212600000002",
        )

    @patch("notifications.services.NotificationService.send_whatsapp_text")
    def test_sweep_sends_whatsapp_follow_up(self, mock_send):
        mock_send.return_value = (True, {})
        req = StaffRequest.objects.create(
            restaurant=self.restaurant,
            subject="WC repair",
            description="Men's restroom",
            category="MAINTENANCE",
            priority="URGENT",
            status="PENDING",
            assignee=self.assignee,
            follow_up_enabled=True,
            whatsapp_notified_at=timezone.now() - timedelta(hours=3),
        )
        summary = staff_request_follow_up_sweep()
        req.refresh_from_db()
        self.assertEqual(summary["followed_up"], 1)
        self.assertEqual(req.follow_up_count, 1)
        mock_send.assert_called_once()

    @patch("notifications.services.notification_service.send_whatsapp_text")
    def test_sweep_escalates_after_max_follow_ups(self, mock_send):
        mock_send.return_value = (True, {})
        req = StaffRequest.objects.create(
            restaurant=self.restaurant,
            subject="WC repair",
            description="Men's restroom",
            category="MAINTENANCE",
            priority="URGENT",
            status="PENDING",
            assignee=self.assignee,
            follow_up_enabled=True,
            follow_up_count=2,
            follow_up_max=2,
            whatsapp_notified_at=timezone.now() - timedelta(hours=10),
        )
        summary = staff_request_follow_up_sweep()
        req.refresh_from_db()
        self.assertEqual(summary["escalated"], 1)
        self.assertIsNotNone(req.escalated_at)
