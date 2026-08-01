"""Tests for Miya widget list routing catalog."""

from __future__ import annotations

from django.test import TestCase

from accounts.models import CustomUser, Restaurant
from dashboard.models import DashboardCustomWidget


class WidgetRoutingCatalogTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Widget Routing Demo")
        self.manager = CustomUser.objects.create_user(
            email="mgr@widgets.test",
            password="testpass123",
            restaurant=self.restaurant,
            role="MANAGER",
        )
        self.widget = DashboardCustomWidget.objects.create(
            user=self.manager,
            restaurant=self.restaurant,
            title="Wedding",
            subtitle="Backyard ceremony prep",
            routing_keywords=["wedding", "ceremony"],
        )

    def test_routing_catalog_includes_keywords(self):
        from dashboard.views_widget_layout import AgentDashboardWidgetListView
        from rest_framework.test import APIRequestFactory

        factory = APIRequestFactory()
        request = factory.post(
            "/api/dashboard/agent/widgets/list/",
            {"user_id": str(self.manager.id)},
            format="json",
        )
        response = AgentDashboardWidgetListView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        body = response.data
        self.assertTrue(body.get("success"))
        catalog = body.get("routing_catalog") or []
        wedding = next((w for w in catalog if w.get("title") == "Wedding"), None)
        self.assertIsNotNone(wedding)
        self.assertIn("wedding", [k.lower() for k in wedding.get("routing_keywords") or []])
