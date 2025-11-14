from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from uuid import uuid4

from accounts.models import CustomUser, Restaurant
from attendance.models import ShiftReview


class ShiftReviewIntegrationTests(APITestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(
            name="Test R",
            address="123 St",
            phone="+10000000000",
            email="r@test.local",
        )
        self.staff = CustomUser.objects.create_user(
            email="staff@test.local",
            password="pass123",
            first_name="Test",
            last_name="Staff",
            role="WAITER",
            restaurant=self.restaurant,
        )

    def test_submit_and_list_shift_reviews(self):
        self.client.force_authenticate(user=self.staff)
        payload = {
            "session_id": str(uuid4()),
            "rating": 5,
            "tags": ["busy", "night"],
            "comments": "Great shift",
            "completed_at_iso": "2024-12-31T23:59:00Z",
            "hours_decimal": 4.5,
        }
        url = "/api/attendance/shift-reviews/"
        resp = self.client.post(url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        data = resp.json()
        self.assertIn("id", data)

        # Verify review is listed
        list_resp = self.client.get(url)
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
        items = list_resp.json()
        self.assertTrue(isinstance(items, list))
        self.assertGreaterEqual(len(items), 1)

        # Basic field expectations
        first = items[0]
        self.assertEqual(first.get("rating"), 5)
        self.assertEqual(first.get("staff"), str(self.staff.id))
        self.assertEqual(first.get("session_id"), payload["session_id"])

    def test_stats_include_orphaned_reviews(self):
        # Create an orphaned review (restaurant=None) for staff of this restaurant
        ShiftReview.objects.create(
            session_id=uuid4(),
            staff=self.staff,
            rating=4,
            tags=["calm"],
            comments="No link",
            completed_at="2024-12-30T22:00:00Z",
            hours_decimal=3.0,
            restaurant=None,
        )

        # Stats require admin/manager role; create a manager user
        manager = CustomUser.objects.create_user(
            email="manager@test.local",
            password="pass123",
            first_name="Mgr",
            last_name="User",
            role="MANAGER",
            restaurant=self.restaurant,
        )
        self.client.force_authenticate(user=manager)
        stats_resp = self.client.get("/api/attendance/shift-reviews/stats/")
        self.assertEqual(stats_resp.status_code, status.HTTP_200_OK)
        stats = stats_resp.json()
        # Should count the orphaned review
        self.assertGreaterEqual(stats.get("total_reviews", 0), 1)