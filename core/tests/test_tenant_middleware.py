"""Phase 3 — TenantContextMiddleware activation tests."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from core.middleware import (
    TenantContextMiddleware,
    inject_tenant_from_user,
    should_skip_tenant_context,
)


class TenantSkipPathTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_whatsapp_webhook_skipped(self):
        req = self.factory.post("/api/notifications/whatsapp/webhook/")
        self.assertTrue(should_skip_tenant_context(req))

    def test_agent_dashboard_skipped(self):
        req = self.factory.post("/api/dashboard/agent/tasks/create/")
        self.assertTrue(should_skip_tenant_context(req))

    def test_mastra_execute_tool_skipped(self):
        req = self.factory.post("/api/miya/mastra/execute-tool/")
        self.assertTrue(should_skip_tenant_context(req))

    def test_public_login_skipped(self):
        req = self.factory.post("/api/accounts/auth/login/")
        self.assertTrue(should_skip_tenant_context(req))

    def test_onboarding_skipped(self):
        req = self.factory.get("/api/accounts/onboarding/")
        self.assertTrue(should_skip_tenant_context(req))

    @patch("core.agent_auth.is_agent_bearer", return_value=True)
    def test_agent_bearer_skipped_on_dashboard_api(self, _mock):
        req = self.factory.post(
            "/api/dashboard/widgets/list/",
            HTTP_AUTHORIZATION="Bearer agent-secret",
        )
        self.assertTrue(should_skip_tenant_context(req))

    def test_dashboard_api_not_skipped_without_auth(self):
        req = self.factory.get("/api/dashboard/widgets/list/")
        self.assertFalse(should_skip_tenant_context(req))


class TenantInjectionTests(SimpleTestCase):
    def test_inject_from_user_with_restaurant(self):
        req = MagicMock()
        user = MagicMock()
        user.is_authenticated = True
        user.is_superuser = False
        rest = MagicMock()
        rest.id = "r1"
        rest.name = "Demo Bistro"
        user.restaurant = rest

        self.assertTrue(inject_tenant_from_user(req, user))
        self.assertEqual(req.tenant_id, "r1")
        self.assertEqual(req.tenant_name, "Demo Bistro")

    def test_superuser_without_restaurant(self):
        req = MagicMock()
        user = MagicMock()
        user.is_authenticated = True
        user.is_superuser = True
        user.restaurant = None

        self.assertTrue(inject_tenant_from_user(req, user))
        self.assertIsNone(req.tenant_id)


@override_settings(MIYA_MASTRA_API_KEY="test-agent-key")
class TenantContextMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = TenantContextMiddleware(lambda req: HttpResponse("ok"))

    def _process(self, req):
        return self.middleware.process_request(req)

    def test_whatsapp_webhook_passes_through(self):
        req = self.factory.post("/api/notifications/whatsapp/webhook/")
        self.assertIsNone(self._process(req))

    def test_agent_path_passes_without_tenant(self):
        req = self.factory.post(
            "/api/scheduling/agent/staff/",
            HTTP_AUTHORIZATION="Bearer test-agent-key",
        )
        self.assertIsNone(self._process(req))
        self.assertFalse(hasattr(req, "tenant_id"))

    @patch("core.middleware.JWTAuthentication")
    def test_jwt_user_gets_tenant_injected(self, mock_jwt_cls):
        user = MagicMock()
        user.is_authenticated = True
        user.is_superuser = False
        rest = MagicMock()
        rest.id = "abc-123"
        rest.name = "Casablanca"
        user.restaurant = rest

        jwt = mock_jwt_cls.return_value
        jwt.get_header.return_value = "header"
        jwt.get_raw_token.return_value = b"token"
        jwt.get_validated_token.return_value = {"sub": "1"}
        jwt.get_user.return_value = user

        req = self.factory.get(
            "/api/dashboard/widgets/list/",
            HTTP_AUTHORIZATION="Bearer user-jwt",
        )
        req.user = MagicMock(is_authenticated=False)

        self.assertIsNone(self._process(req))
        self.assertEqual(req.tenant_id, "abc-123")
        self.assertEqual(req.tenant_name, "Casablanca")

    @patch("core.middleware.JWTAuthentication")
    def test_jwt_user_without_restaurant_gets_403(self, mock_jwt_cls):
        user = MagicMock()
        user.is_authenticated = True
        user.is_superuser = False
        user.restaurant = None
        user.email = "orphan@example.com"

        jwt = mock_jwt_cls.return_value
        jwt.get_header.return_value = "header"
        jwt.get_raw_token.return_value = b"token"
        jwt.get_validated_token.return_value = {"sub": "1"}
        jwt.get_user.return_value = user

        req = self.factory.get(
            "/api/dashboard/widgets/list/",
            HTTP_AUTHORIZATION="Bearer user-jwt",
        )
        req.user = MagicMock(is_authenticated=False)

        response = self._process(req)
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_dashboard_passthrough(self):
        req = self.factory.get("/api/dashboard/widgets/list/")
        req.user = MagicMock(is_authenticated=False)
        self.assertIsNone(self._process(req))
