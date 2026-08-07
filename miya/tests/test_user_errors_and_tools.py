"""Tests for Miya parity fixes — error sanitization and tool registry."""

from django.test import SimpleTestCase

from miya.services.tools import TOOL_SCHEMAS, _ROUTE_MAP
from miya.services.user_errors import pick_user_message, sanitize_user_error


class UserErrorSanitizeTests(SimpleTestCase):
    def test_strips_restaurant_id_from_errors(self):
        raw = "Unable to resolve restaurant context (no restaurant_id/sessionId provided)."
        out = sanitize_user_error(raw)
        self.assertNotIn("restaurant_id", out.lower())
        self.assertIn("workspace", out.lower())

    def test_prefers_message_for_user(self):
        msg = pick_user_message(
            {
                "error": "restaurant_id is required",
                "message_for_user": "I need the vendor name.",
            }
        )
        self.assertEqual(msg, "I need the vendor name.")


class ToolRegistryTests(SimpleTestCase):
    def test_all_tools_have_routes(self):
        from miya.services.ops import CANONICAL_TOOL_NAMES

        names = {(s.get("function") or {}).get("name") for s in TOOL_SCHEMAS}
        # Canonical ops tools may be DB-backed without an HTTP route.
        routed = set(_ROUTE_MAP.keys()) | set(CANONICAL_TOOL_NAMES)
        self.assertTrue(names.issubset(routed), sorted(names - routed))
        # Every HTTP route still has a schema.
        self.assertTrue(set(_ROUTE_MAP.keys()).issubset(names), sorted(set(_ROUTE_MAP.keys()) - names))

    def test_list_shifts_uses_get(self):
        from miya.services.tools import _GET_METHOD_TOOLS, _ROUTE_MAP

        self.assertEqual(_ROUTE_MAP["list_shifts"][0], "GET")
        self.assertIn("list_shifts", _GET_METHOD_TOOLS)

    def test_new_parity_tools_registered(self):
        for name in (
            "chase_operational_record",
            "record_invoice",
            "payment_approval",
            "category_routing",
            "create_custom_widget",
        ):
            self.assertIn(name, _ROUTE_MAP)

    def test_get_only_tools_use_get_method(self):
        from miya.services.tools import _GET_METHOD_TOOLS, _ROUTE_MAP

        for name in (
            "list_staff_requests",
            "list_inventory",
            "sales_summary",
            "list_shifts",
            "my_shifts",
        ):
            self.assertIn(name, _GET_METHOD_TOOLS)
            self.assertEqual(_ROUTE_MAP[name][0], "GET")
