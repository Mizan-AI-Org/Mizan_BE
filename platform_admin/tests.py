"""Permission gate for platform_admin."""
from unittest.mock import MagicMock
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory

from platform_admin.permissions import (
    IsPlatformOperator,
    IsPlatformSuperuser,
    user_is_platform_ops_account,
)


class PlatformOpsAccountTests(SimpleTestCase):
    def test_flagged_operator_is_ops_account(self):
        user = MagicMock(is_platform_operator=True, email="ops@example.com")
        self.assertTrue(user_is_platform_ops_account(user))

    def test_tenant_user_is_not_ops_account(self):
        user = MagicMock(is_platform_operator=False, email="owner@tenant.com")
        self.assertFalse(user_is_platform_ops_account(user))

    @override_settings(PLATFORM_OPS_EMAILS=["you@heymizan.ai"])
    def test_env_email_is_ops_account(self):
        user = MagicMock(is_platform_operator=False, email="you@heymizan.ai")
        self.assertTrue(user_is_platform_ops_account(user))

    @override_settings(PLATFORM_OPS_SUPERUSER_EMAILS=["su@heymizan.ai"])
    def test_superuser_env_email_is_ops_account(self):
        user = MagicMock(is_platform_operator=False, email="su@heymizan.ai")
        self.assertTrue(user_is_platform_ops_account(user))


class IsPlatformOperatorTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.perm = IsPlatformOperator()

    def test_platform_operator_allowed(self):
        user = MagicMock(
            is_authenticated=True,
            is_staff=True,
            is_platform_operator=True,
            email="ops@example.com",
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
            email="owner@tenant.com",
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
            email="staff@example.com",
        )
        req = self.factory.get("/api/platform/me/")
        req.user = user
        self.assertFalse(self.perm.has_permission(req, None))

    def test_anonymous_denied(self):
        user = MagicMock(
            is_authenticated=False,
            is_staff=False,
            is_platform_operator=False,
            email="",
        )
        req = self.factory.get("/api/platform/me/")
        req.user = user
        self.assertFalse(self.perm.has_permission(req, None))

    @override_settings(PLATFORM_OPS_EMAILS=["you@heymizan.ai"])
    def test_env_email_allowed_without_db_flag(self):
        user = MagicMock(
            is_authenticated=True,
            is_staff=False,
            is_platform_operator=False,
            email="you@heymizan.ai",
        )
        req = self.factory.get("/api/platform/me/")
        req.user = user
        self.assertTrue(self.perm.has_permission(req, None))


class IsPlatformSuperuserTests(SimpleTestCase):
    def test_operator_non_superuser_denied(self):
        perm = IsPlatformSuperuser()
        user = MagicMock(
            is_authenticated=True,
            is_staff=True,
            is_platform_operator=True,
            is_superuser=False,
            email="ops@example.com",
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
            email="ops@example.com",
        )
        req = APIRequestFactory().get("/")
        req.user = user
        self.assertTrue(perm.has_permission(req, None))

    @override_settings(
        PLATFORM_OPS_EMAILS=["you@heymizan.ai"],
        PLATFORM_OPS_SUPERUSER_EMAILS=["you@heymizan.ai"],
    )
    def test_env_superuser_email_allowed(self):
        perm = IsPlatformSuperuser()
        user = MagicMock(
            is_authenticated=True,
            is_staff=False,
            is_platform_operator=False,
            is_superuser=False,
            email="you@heymizan.ai",
        )
        req = APIRequestFactory().get("/")
        req.user = user
        self.assertTrue(perm.has_permission(req, None))
