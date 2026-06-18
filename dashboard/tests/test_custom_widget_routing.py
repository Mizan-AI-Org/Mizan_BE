"""Tests for custom widget task routing."""

from __future__ import annotations

from django.test import TestCase

from accounts.models import CustomUser, Restaurant
from dashboard.custom_widget_routing import match_custom_widget_for_task
from dashboard.models import DashboardCustomWidget


class CustomWidgetRoutingTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Kasbah Demo")
        self.user = CustomUser.objects.create_user(
            email="manager@kasbah.test",
            password="testpass123",
            restaurant=self.restaurant,
            role="MANAGER",
        )
        self.widget = DashboardCustomWidget.objects.create(
            user=self.user,
            restaurant=self.restaurant,
            title="Event Kasbah Dif",
            subtitle="Menus, setup, and on-site prep",
        )

    def test_matches_kasbah_event_task_by_title_overlap(self):
        matched = match_custom_widget_for_task(
            user=self.user,
            restaurant=self.restaurant,
            title="Print menus for the Kasbah Dif event",
            description="Prepare printed menus before guests arrive",
        )
        self.assertIsNotNone(matched)
        self.assertEqual(matched.id, self.widget.id)

    def test_explicit_widget_id_wins(self):
        other = DashboardCustomWidget.objects.create(
            user=self.user,
            restaurant=self.restaurant,
            title="Other lane",
        )
        matched = match_custom_widget_for_task(
            user=self.user,
            restaurant=self.restaurant,
            title="Unrelated task",
            explicit_id=str(other.id),
        )
        self.assertEqual(matched.id, other.id)

    def test_no_match_for_unrelated_task(self):
        matched = match_custom_widget_for_task(
            user=self.user,
            restaurant=self.restaurant,
            title="Reply to tripadvisor review",
            description="Customer feedback",
        )
        self.assertIsNone(matched)

    def test_custom_prefix_slot_id_accepted(self):
        matched = match_custom_widget_for_task(
            user=self.user,
            restaurant=self.restaurant,
            title="Anything",
            explicit_id=f"custom:{self.widget.id}",
        )
        self.assertEqual(matched.id, self.widget.id)
