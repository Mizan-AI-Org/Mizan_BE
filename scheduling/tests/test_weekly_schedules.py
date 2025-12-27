from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser, Restaurant
from scheduling.models import WeeklySchedule


class WeeklySchedulesApiTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.restaurant = Restaurant.objects.create(
            name="Test R",
            email="r@test.local",
        )
        self.admin = CustomUser.objects.create_user(
            email="admin@test.local",
            password="AdminPass123!",
            role="ADMIN",
            restaurant=self.restaurant,
            first_name="Admin",
            last_name="User",
        )
        self.client.force_authenticate(user=self.admin)
        self.base_url = "/api/scheduling/weekly-schedules/"

    def test_list_empty(self):
        resp = self.client.get(self.base_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        if isinstance(data, dict) and "results" in data:
            self.assertEqual(len(data["results"]), 0)
        else:
            self.assertEqual(len(data), 0)

    def test_create_and_prevent_duplicate(self):
        week_start = timezone.now().date()
        # normalize to Monday
        week_start = week_start - timezone.timedelta(days=(week_start.weekday()))
        week_end = week_start + timezone.timedelta(days=6)

        payload = {
            "week_start": str(week_start),
            "week_end": str(week_end),
            "is_published": False,
        }
        r1 = self.client.post(self.base_url, payload, format="json")
        self.assertEqual(r1.status_code, status.HTTP_201_CREATED)
        self.assertTrue(WeeklySchedule.objects.filter(restaurant=self.restaurant, week_start=week_start).exists())

        r2 = self.client.post(self.base_url, payload, format="json")
        self.assertEqual(r2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", r2.json())

