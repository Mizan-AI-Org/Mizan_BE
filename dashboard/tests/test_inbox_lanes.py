"""Tests for widget-driven staff-request inbox lanes."""

from django.test import SimpleTestCase

from dashboard.inbox_lanes import (
    WIDGET_INBOX_LANES,
    inbox_lanes_for_widget_order,
    resolve_lane_id,
)


class InboxLanesForWidgetOrderTests(SimpleTestCase):
    def test_empty_order_returns_no_lanes(self):
        self.assertEqual(inbox_lanes_for_widget_order([]), [])

    def test_only_enabled_widgets_create_tabs(self):
        order = ["insights", "team_travel", "team_medical_service", "staffing"]
        lanes = inbox_lanes_for_widget_order(order)
        self.assertEqual([lane["lane_id"] for lane in lanes], ["team_travel", "team_medical_service"])

    def test_lane_order_follows_dashboard_order(self):
        order = ["finance", "team_travel", "human_resources"]
        lanes = inbox_lanes_for_widget_order(order)
        self.assertEqual([lane["lane_id"] for lane in lanes], ["finance", "team_travel", "human_resources"])

    def test_finance_lane_aggregates_payroll_and_finance(self):
        lane = WIDGET_INBOX_LANES["finance"]
        self.assertEqual(set(lane.categories), {"FINANCE", "PAYROLL"})


class ResolveLaneIdTests(SimpleTestCase):
    def setUp(self):
        self.lanes = inbox_lanes_for_widget_order(
            ["team_travel", "team_medical_service", "finance"]
        )

    def test_resolve_by_lane_param(self):
        self.assertEqual(
            resolve_lane_id(lane_id="team_medical_service", enabled_lanes=self.lanes),
            "team_medical_service",
        )

    def test_unknown_lane_returns_none(self):
        self.assertIsNone(resolve_lane_id(lane_id="maintenance", enabled_lanes=self.lanes))

    def test_resolve_single_category_to_lane(self):
        self.assertEqual(
            resolve_lane_id(categories=["MEDICAL"], enabled_lanes=self.lanes),
            "team_medical_service",
        )

    def test_resolve_multi_category_bucket(self):
        self.assertEqual(
            resolve_lane_id(categories=["FINANCE", "PAYROLL"], enabled_lanes=self.lanes),
            "finance",
        )
