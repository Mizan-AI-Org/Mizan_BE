"""
Unit-style tests for staff branch transfer endpoint.
"""

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import BusinessLocation, CustomUser, Restaurant


class StaffTransferLocationsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.restaurant = Restaurant.objects.create(name="Test Café")
        self.loc_a = BusinessLocation.objects.create(
            restaurant=self.restaurant, name="Branch A", is_primary=True, is_active=True
        )
        self.loc_b = BusinessLocation.objects.create(
            restaurant=self.restaurant, name="Branch B", is_primary=False, is_active=True
        )
        self.admin = CustomUser.objects.create_user(
            email="admin@test.com",
            password="pass12345",
            restaurant=self.restaurant,
            role="ADMIN",
            first_name="Ada",
            last_name="Min",
        )
        self.staff = CustomUser.objects.create_user(
            email="adam@test.com",
            password="pass12345",
            restaurant=self.restaurant,
            role="WAITER",
            first_name="Adam",
            last_name="Jarjusey",
            primary_location=self.loc_a,
        )
        self.staff2 = CustomUser.objects.create_user(
            email="josh@test.com",
            password="pass12345",
            restaurant=self.restaurant,
            role="WAITER",
            first_name="Joshua",
            last_name="Mwaniki",
            primary_location=self.loc_a,
        )
        self.client.force_authenticate(user=self.admin)

    def test_move_single_staff(self):
        resp = self.client.post(
            "/api/staff/transfer-locations/",
            {
                "staff_ids": [str(self.staff.id)],
                "primary_location": str(self.loc_b.id),
                "allowed_mode": "add_destination",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.data["success"])
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.primary_location_id, self.loc_b.id)

    def test_move_multiple_staff(self):
        resp = self.client.post(
            "/api/staff/transfer-locations/",
            {
                "staff_ids": [str(self.staff.id), str(self.staff2.id)],
                "primary_location": str(self.loc_b.id),
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["moved_count"], 2)
        self.staff.refresh_from_db()
        self.staff2.refresh_from_db()
        self.assertEqual(self.staff.primary_location_id, self.loc_b.id)
        self.assertEqual(self.staff2.primary_location_id, self.loc_b.id)
