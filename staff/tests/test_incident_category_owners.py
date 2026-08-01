"""Tests for incident routing from category_owners settings."""

from django.test import TestCase

from accounts.models import CustomUser, Restaurant
from staff.incident_routing import (
    normalize_incident_category_for_storage,
    resolve_all_assignees_for_incident_type,
    resolve_default_assignee_for_incident_type,
)


class IncidentCategoryOwnersRoutingTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Test Bistro")
        self.owner_a = CustomUser.objects.create_user(
            email="fatima@test.com",
            password="pass12345",
            first_name="Fatima",
            last_name="Zahra",
            role="MANAGER",
            restaurant=self.restaurant,
        )
        self.owner_b = CustomUser.objects.create_user(
            email="admin@test.com",
            password="pass12345",
            first_name="Admin",
            last_name="User",
            role="ADMIN",
            restaurant=self.restaurant,
        )
        self.restaurant.general_settings = {
            "category_owners": {
                "incident.safety": [str(self.owner_a.id), str(self.owner_b.id)],
                "incident.equipment": [str(self.owner_b.id)],
            }
        }
        self.restaurant.save(update_fields=["general_settings"])

    def test_resolve_all_safety_owners(self):
        owners = resolve_all_assignees_for_incident_type(self.restaurant, "Safety")
        ids = {str(u.id) for u in owners}
        self.assertEqual(ids, {str(self.owner_a.id), str(self.owner_b.id)})

    def test_resolve_primary_is_first_owner(self):
        primary = resolve_default_assignee_for_incident_type(self.restaurant, "Safety")
        self.assertEqual(primary.id, self.owner_a.id)

    def test_maintenance_maps_to_equipment_slug(self):
        owners = resolve_all_assignees_for_incident_type(self.restaurant, "Maintenance")
        self.assertEqual(len(owners), 1)
        self.assertEqual(owners[0].id, self.owner_b.id)

    def test_service_normalizes_to_customer_issue(self):
        self.restaurant.general_settings["category_owners"]["incident.customer"] = [
            str(self.owner_a.id)
        ]
        self.restaurant.save(update_fields=["general_settings"])
        owners = resolve_all_assignees_for_incident_type(self.restaurant, "Service")
        self.assertEqual(len(owners), 1)
        self.assertEqual(owners[0].id, self.owner_a.id)
        self.assertEqual(
            normalize_incident_category_for_storage("Service"), "Customer Issue"
        )
