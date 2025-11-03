from django.test import TestCase
from rest_framework.test import APIClient
from django.urls import reverse

from accounts.models import CustomUser, Restaurant


class SettingsRBACPermissionsTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.restaurant = Restaurant.objects.create(
            name="Test Restaurant",
            email="rest@test.com"
        )

        # Admin user
        self.admin = CustomUser.objects.create_user(
            email="admin@test.com",
            password="AdminPass123!",
            role="ADMIN",
            restaurant=self.restaurant,
            first_name="Admin",
            last_name="User"
        )

        # Staff user (PIN-based)
        self.staff = CustomUser.objects.create_user(
            email="staff@test.com",
            pin_code="1234",
            role="CASHIER",
            restaurant=self.restaurant,
            first_name="Staff",
            last_name="User"
        )

        # Base endpoints under router basename 'settings'
        self.unified_url = "/api/settings/unified/"
        self.geolocation_url = "/api/settings/geolocation/"
        self.pos_url = "/api/settings/pos_integration/"
        self.pos_test_url = "/api/settings/test_pos_connection/"

    def test_staff_get_unified_settings_forbidden(self):
        self.client.force_authenticate(user=self.staff)
        resp = self.client.get(self.unified_url)
        self.assertEqual(resp.status_code, 403)

    def test_staff_update_unified_settings_forbidden(self):
        self.client.force_authenticate(user=self.staff)
        resp = self.client.put(self.unified_url, {"general": {"timezone": "UTC"}}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_staff_geolocation_settings_forbidden(self):
        self.client.force_authenticate(user=self.staff)
        resp_get = self.client.get(self.geolocation_url)
        self.assertEqual(resp_get.status_code, 403)
        resp_post = self.client.post(self.geolocation_url, {"latitude": 1.0, "longitude": 2.0}, format="json")
        self.assertEqual(resp_post.status_code, 403)

    def test_staff_pos_settings_forbidden(self):
        self.client.force_authenticate(user=self.staff)
        resp_get = self.client.get(self.pos_url)
        self.assertEqual(resp_get.status_code, 403)
        resp_post = self.client.post(self.pos_url, {"pos_provider": "STRIPE"}, format="json")
        self.assertEqual(resp_post.status_code, 403)
        resp_test = self.client.post(self.pos_test_url, {}, format="json")
        self.assertEqual(resp_test.status_code, 403)

    def test_admin_can_access_all_settings_endpoints(self):
        self.client.force_authenticate(user=self.admin)
        # Unified
        r1 = self.client.get(self.unified_url)
        self.assertIn(r1.status_code, [200, 204])
        r2 = self.client.put(self.unified_url, {"general": {"timezone": "UTC"}}, format="json")
        self.assertIn(r2.status_code, [200, 204])
        # Geolocation
        g1 = self.client.get(self.geolocation_url)
        self.assertIn(g1.status_code, [200, 204])
        g2 = self.client.post(self.geolocation_url, {"latitude": 1.0, "longitude": 2.0}, format="json")
        self.assertIn(g2.status_code, [200, 201, 204])
        # POS
        p1 = self.client.get(self.pos_url)
        self.assertIn(p1.status_code, [200, 204])
        p2 = self.client.post(self.pos_url, {"pos_provider": "STRIPE"}, format="json")
        self.assertIn(p2.status_code, [200, 201, 204])
        p3 = self.client.post(self.pos_test_url, {}, format="json")
        self.assertIn(p3.status_code, [200, 204])

    def test_staff_can_access_geofence_validation(self):
        # Validate that non-settings helper remains accessible to staff
        self.client.force_authenticate(user=self.staff)
        validate_url = "/api/settings/validate_geolocation/"
        resp = self.client.post(validate_url, {"lat": 1.0, "lng": 2.0}, format="json")
        # Not enforcing a status code; just ensure not 403
        self.assertNotEqual(resp.status_code, 403)