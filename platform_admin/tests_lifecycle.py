from types import SimpleNamespace

from django.test import SimpleTestCase

from platform_admin.lifecycle import (
    flag_truthy,
    restaurant_access_denied_reason,
    tenant_lifecycle,
    user_tenant_access_denied_reason,
)


class TenantLifecycleTests(SimpleTestCase):
    def test_truthy_shapes(self):
        for value in (True, "true", "True", "TRUE", "1", 1, "yes", "on"):
            self.assertTrue(flag_truthy(value), value)

    def test_falsey_shapes(self):
        for value in (False, None, "", "false", "0", 0, "no", "off"):
            self.assertFalse(flag_truthy(value), value)

    def test_active_excludes_suspended_and_deactivated(self):
        self.assertEqual(tenant_lifecycle({}), "active")
        self.assertEqual(tenant_lifecycle({"platform_suspended": False}), "active")
        self.assertEqual(tenant_lifecycle({"platform_suspended": True}), "suspended")
        self.assertEqual(tenant_lifecycle({"platform_suspended": "True"}), "suspended")
        self.assertEqual(
            tenant_lifecycle({"platform_deactivated": True, "platform_suspended": True}),
            "deactivated",
        )

    def test_restaurant_access_denied_suspended_and_deactivated(self):
        self.assertIsNone(restaurant_access_denied_reason(None))
        active = SimpleNamespace(general_settings={})
        self.assertIsNone(restaurant_access_denied_reason(active))
        suspended = SimpleNamespace(general_settings={"platform_suspended": True})
        self.assertIn("suspended", restaurant_access_denied_reason(suspended).lower())
        deactivated = SimpleNamespace(general_settings={"platform_deactivated": "1"})
        self.assertIn("deactivated", restaurant_access_denied_reason(deactivated).lower())

    def test_user_denied_when_inactive_or_tenant_blocked(self):
        inactive = SimpleNamespace(
            is_active=False,
            is_platform_operator=False,
            restaurant=SimpleNamespace(general_settings={}),
        )
        self.assertIn("deactivated", user_tenant_access_denied_reason(inactive).lower())

        suspended_user = SimpleNamespace(
            is_active=True,
            is_platform_operator=False,
            restaurant=SimpleNamespace(general_settings={"platform_suspended": True}),
        )
        self.assertIn("suspended", user_tenant_access_denied_reason(suspended_user).lower())

        ops = SimpleNamespace(
            is_active=True,
            is_platform_operator=True,
            restaurant=SimpleNamespace(general_settings={"platform_suspended": True}),
        )
        # Ops accounts are exempt even if attached to a blocked restaurant.
        # user_is_platform_ops_account may not see SimpleNamespace; fall back flag works.
        self.assertIsNone(user_tenant_access_denied_reason(ops))
