"""Tests for in-process Miya agent tool dispatch."""

from django.conf import settings
from django.test import TestCase

from miya.services.tool_dispatch import dispatch_agent_request, should_dispatch_in_process


class ToolDispatchTests(TestCase):
    def test_should_dispatch_in_process_always_true(self):
        self.assertTrue(should_dispatch_in_process("http://127.0.0.1:8000"))
        self.assertTrue(should_dispatch_in_process("https://api.heymizan.ai"))

    def test_list_tasks_in_process(self):
        key = getattr(settings, "LUA_WEBHOOK_API_KEY", "")
        if not key:
            self.skipTest("LUA_WEBHOOK_API_KEY not configured")

        from accounts.models import Restaurant

        rest = Restaurant.objects.first()
        if not rest:
            self.skipTest("No restaurant in database")

        headers = {
            "Authorization": f"Bearer {key}",
            "X-Restaurant-Id": str(rest.id),
        }
        code, body = dispatch_agent_request(
            "POST",
            "/api/dashboard/agent/tasks/list/",
            json_payload={"limit": 5},
            headers=headers,
        )
        self.assertEqual(code, 200)
        self.assertTrue(body.get("success"))
