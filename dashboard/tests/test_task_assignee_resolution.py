"""Tests for task assignee resolution and category routing engine."""

from unittest.mock import patch

from django.test import TestCase

from accounts.models import CustomUser, Restaurant
from dashboard.views_agent import _resolve_assignee
from staff.category_routing_engine import (
    STRATEGY_NOTIFY_ALL,
    STRATEGY_ROUND_ROBIN,
    resolve_routing_for_staff_category,
)


class TaskAssigneeResolutionTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Routing Bistro", slug="routing-bistro")
        self.karim = CustomUser.objects.create_user(
            email="karim@test.com",
            password="pass12345",
            restaurant=self.restaurant,
            first_name="Karim",
            last_name="Benali",
            role="STAFF",
        )

    def test_resolve_assignee_by_assignee_id(self):
        user, err = _resolve_assignee(
            {"assignee_id": str(self.karim.id)},
            self.restaurant,
        )
        self.assertIsNone(err)
        self.assertEqual(user.id, self.karim.id)

    def test_resolve_assignee_by_assigneeId_camel_case(self):
        user, err = _resolve_assignee(
            {"assigneeId": str(self.karim.id)},
            self.restaurant,
        )
        self.assertIsNone(err)
        self.assertEqual(user.id, self.karim.id)


class CategoryRoutingEngineTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Owners Bistro", slug="owners-bistro")
        self.owner_a = CustomUser.objects.create_user(
            email="a@test.com",
            password="pass12345",
            restaurant=self.restaurant,
            first_name="John",
            role="MANAGER",
        )
        self.owner_b = CustomUser.objects.create_user(
            email="b@test.com",
            password="pass12345",
            restaurant=self.restaurant,
            first_name="Sarah",
            role="MANAGER",
        )
        self.restaurant.general_settings = {
            "category_owners": {
                "request.finance": [str(self.owner_a.id), str(self.owner_b.id)],
            },
            "category_routing": {
                "request.finance": {"strategy": STRATEGY_NOTIFY_ALL},
            },
        }
        self.restaurant.save(update_fields=["general_settings"])

    def test_notify_all_returns_both_owners_as_targets(self):
        result = resolve_routing_for_staff_category(self.restaurant, "FINANCE")
        self.assertEqual(result.primary.id, self.owner_a.id)
        self.assertEqual(len(result.owners), 2)
        self.assertEqual(len(result.notify_targets), 2)
        self.assertEqual(result.strategy, STRATEGY_NOTIFY_ALL)
        self.assertEqual(len(result.informed), 1)

    def test_round_robin_rotates_primary(self):
        gs = dict(self.restaurant.general_settings or {})
        gs["category_routing"] = {
            "request.finance": {"strategy": STRATEGY_ROUND_ROBIN},
        }
        self.restaurant.general_settings = gs
        self.restaurant.save(update_fields=["general_settings"])

        first = resolve_routing_for_staff_category(self.restaurant, "FINANCE")
        second = resolve_routing_for_staff_category(self.restaurant, "FINANCE")
        self.assertNotEqual(first.primary.id, second.primary.id)
