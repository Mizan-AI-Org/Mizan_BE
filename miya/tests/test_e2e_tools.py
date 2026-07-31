"""E2E tests for Miya tool wiring (in-process dispatch)."""

from django.conf import settings
from django.test import TestCase

from miya.services.tool_dispatch import dispatch_agent_request
from miya.services.tools import TOOL_SCHEMAS, _ROUTE_MAP
from accounts.rbac_enforce import allowed_tools_for_user


class MiyaToolRegistryTests(TestCase):
    def test_all_routes_have_schemas(self):
        schema_names = {(s.get("function") or {}).get("name") for s in TOOL_SCHEMAS}
        for name in _ROUTE_MAP:
            self.assertIn(name, schema_names, f"Missing TOOL_SCHEMAS entry for {name}")

    def test_new_task_tools_registered(self):
        for name in (
            "update_dashboard_task_status",
            "reassign_dashboard_task",
            "update_dashboard_task",
            "create_calendar_event",
            "create_personal_reminder",
            "list_invoices",
            "ops_search",
        ):
            self.assertIn(name, _ROUTE_MAP)

    def test_list_tasks_dispatch(self):
        key = getattr(settings, "MIYA_MASTRA_API_KEY", "")
        if not key:
            self.skipTest("MIYA_MASTRA_API_KEY not configured")

        from accounts.models import Restaurant

        rest = Restaurant.objects.first()
        if not rest:
            self.skipTest("No restaurant")

        headers = {
            "Authorization": f"Bearer {key}",
            "X-Restaurant-Id": str(rest.id),
        }
        code, body = dispatch_agent_request(
            "POST",
            "/api/dashboard/agent/tasks/list/",
            json_payload={"limit": 3, "restaurant_id": str(rest.id)},
            headers=headers,
        )
        self.assertEqual(code, 200)
        self.assertTrue(body.get("success"))

    def test_ops_search_dispatch_accepts_body_q(self):
        key = getattr(settings, "MIYA_MASTRA_API_KEY", "")
        if not key:
            self.skipTest("MIYA_MASTRA_API_KEY not configured")

        from accounts.models import Restaurant

        rest = Restaurant.objects.first()
        if not rest:
            self.skipTest("No restaurant")

        headers = {
            "Authorization": f"Bearer {key}",
            "X-Restaurant-Id": str(rest.id),
        }
        code, body = dispatch_agent_request(
            "GET",
            "/api/dashboard/agent/search/",
            json_payload={"q": "test", "restaurant_id": str(rest.id)},
            headers=headers,
        )
        self.assertIn(code, (200, 400))
        if code == 200:
            self.assertIn("staff", body)


class MiyaRBACTests(TestCase):
    def test_manager_gets_full_tool_set(self):
        from accounts.models import CustomUser
        from accounts.rbac_enforce import miya_has_full_tenant_access

        mgr = CustomUser.objects.filter(role="MANAGER", is_active=True).first()
        if not mgr or not getattr(mgr, "restaurant", None):
            self.skipTest("No manager with restaurant")
        self.assertTrue(miya_has_full_tenant_access(mgr, mgr.restaurant))
        tools = allowed_tools_for_user(mgr, mgr.restaurant)
        self.assertIn("update_dashboard_task_status", tools)
        self.assertIn("create_calendar_event", tools)
