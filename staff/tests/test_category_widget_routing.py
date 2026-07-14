"""Tests for category → dashboard widget routing."""

from django.test import SimpleTestCase, TestCase

from accounts.models import CustomUser, Restaurant
from dashboard.category_routing import (
    category_lane_hint,
    dashboard_widgets_for_category,
    ensure_dashboard_widgets_for_managers,
    primary_widget_for_category,
)


class CategoryRoutingMappingTests(SimpleTestCase):
    def test_maintenance_maps_to_maintenance_widget(self):
        self.assertEqual(primary_widget_for_category("MAINTENANCE"), "maintenance")

    def test_scheduling_maps_to_team_travel(self):
        self.assertEqual(primary_widget_for_category("SCHEDULING"), "team_travel")

    def test_dashboard_widgets_pin_primary_lane_only(self):
        widgets = dashboard_widgets_for_category("PURCHASE_ORDER")
        self.assertNotIn("staff_inbox", widgets)
        self.assertIn("purchase_orders", widgets)

    def test_payroll_pins_human_resources_only(self):
        widgets = dashboard_widgets_for_category("PAYROLL")
        self.assertEqual(widgets, ["human_resources"])

    def test_lane_hint_mentions_widget(self):
        hint = category_lane_hint("FINANCE")
        self.assertIn("finance", hint.lower())


class EnsureDashboardWidgetsTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Route Bistro", slug="route-bistro")
        self.manager = CustomUser.objects.create_user(
            email="mgr@route.com",
            password="pass12345",
            restaurant=self.restaurant,
            role="MANAGER",
        )

    def test_pins_primary_widget_for_manager(self):
        result = ensure_dashboard_widgets_for_managers(
            self.restaurant,
            category="MAINTENANCE",
        )
        self.manager.refresh_from_db()
        order = self.manager.dashboard_widget_order or []
        self.assertNotIn("staff_inbox", result["widgets"])
        self.assertIn("maintenance", order)
        self.assertGreaterEqual(len(result["managers_updated"]), 1)
