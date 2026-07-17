"""Permission gate for platform_admin."""
from unittest.mock import MagicMock
from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory

from platform_admin.permissions import IsPlatformOperator, IsPlatformSuperuser


class IsPlatformOperatorTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.perm = IsPlatformOperator()

    def test_platform_operator_allowed(self):
        user = MagicMock(
            is_authenticated=True,
            is_staff=True,
            is_platform_operator=True,
            restaurant_id=None,
        )
        req = self.factory.get("/api/platform/me/")
        req.user = user
        self.assertTrue(self.perm.has_permission(req, None))

    def test_restaurant_super_admin_with_staff_denied(self):
        """Tenant SUPER_ADMIN accidentally marked is_staff must not get /admin."""
        user = MagicMock(
            is_authenticated=True,
            is_staff=True,
            is_superuser=True,
            is_platform_operator=False,
            role="SUPER_ADMIN",
            restaurant_id="abc",
        )
        req = self.factory.get("/api/platform/me/")
        req.user = user
        self.assertFalse(self.perm.has_permission(req, None))

    def test_staff_without_operator_flag_denied(self):
        user = MagicMock(
            is_authenticated=True,
            is_staff=True,
            is_platform_operator=False,
        )
        req = self.factory.get("/api/platform/me/")
        req.user = user
        self.assertFalse(self.perm.has_permission(req, None))

    def test_anonymous_denied(self):
        user = MagicMock(is_authenticated=False, is_staff=False, is_platform_operator=False)
        req = self.factory.get("/api/platform/me/")
        req.user = user
        self.assertFalse(self.perm.has_permission(req, None))


class IsPlatformSuperuserTests(SimpleTestCase):
    def test_operator_non_superuser_denied(self):
        perm = IsPlatformSuperuser()
        user = MagicMock(
            is_authenticated=True,
            is_staff=True,
            is_platform_operator=True,
            is_superuser=False,
        )
        req = APIRequestFactory().get("/")
        req.user = user
        self.assertFalse(perm.has_permission(req, None))

    def test_operator_superuser_allowed(self):
        perm = IsPlatformSuperuser()
        user = MagicMock(
            is_authenticated=True,
            is_staff=True,
            is_platform_operator=True,
            is_superuser=True,
        )
        req = APIRequestFactory().get("/")
        req.user = user
        self.assertTrue(perm.has_permission(req, None))
