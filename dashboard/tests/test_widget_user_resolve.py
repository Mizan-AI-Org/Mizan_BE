"""Tests for Miya dashboard widget user resolution (admin LuaPop)."""

from __future__ import annotations

from django.test import RequestFactory, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import CustomUser, Restaurant
from dashboard.views_widget_layout import _resolve_user_from_agent_payload


@override_settings(LUA_WEBHOOK_API_KEY="test-agent-key")
class WidgetUserResolveTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.restaurant = Restaurant.objects.create(name="Jah Jah Demo")
        self.manager = CustomUser.objects.create_user(
            email="admin@jahjah.test",
            password="testpass123",
            restaurant=self.restaurant,
            role="ADMIN",
        )

    def test_resolves_user_from_session_id_in_body(self):
        session_id = f"tenant-{self.restaurant.id}-user-{self.manager.id}"
        request = self.factory.post(
            "/api/dashboard/agent/widgets/create/",
            data={"title": "Jah Jah Crates", "restaurant_id": str(self.restaurant.id)},
            content_type="application/json",
        )
        user = _resolve_user_from_agent_payload(
            {"session_id": session_id, "restaurant_id": str(self.restaurant.id)},
            request,
        )
        self.assertEqual(user.id, self.manager.id)

    def test_resolves_user_from_x_session_id_header(self):
        session_id = f"tenant-{self.restaurant.id}-user-{self.manager.id}"
        request = self.factory.post(
            "/api/dashboard/agent/widgets/create/",
            data={"title": "Jah Jah Crates"},
            content_type="application/json",
            HTTP_X_SESSION_ID=session_id,
        )
        user = _resolve_user_from_agent_payload({"title": "Jah Jah Crates"}, request)
        self.assertEqual(user.id, self.manager.id)

    def test_resolves_user_from_email_address_field(self):
        user = _resolve_user_from_agent_payload({"emailAddress": self.manager.email})
        self.assertEqual(user.id, self.manager.id)

    def test_resolves_user_from_metadata_user_id(self):
        user = _resolve_user_from_agent_payload(
            {"metadata": {"userId": str(self.manager.id)}}
        )
        self.assertEqual(user.id, self.manager.id)

    def test_resolves_user_from_x_user_token_header(self):
        token = str(AccessToken.for_user(self.manager))
        request = self.factory.post(
            "/api/dashboard/agent/widgets/create/",
            data={"title": "Jah Jah Crates", "restaurant_id": str(self.restaurant.id)},
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer test-agent-key",
            HTTP_X_USER_TOKEN=f"Bearer {token}",
        )
        user = _resolve_user_from_agent_payload(
            {"title": "Jah Jah Crates", "restaurant_id": str(self.restaurant.id)},
            request,
        )
        self.assertEqual(user.id, self.manager.id)
