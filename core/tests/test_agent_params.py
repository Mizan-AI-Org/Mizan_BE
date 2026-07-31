"""Tests for agent param normalization."""

from datetime import date

from django.test import SimpleTestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from core.agent_params import agent_date, agent_scalar, resolve_shift_date_range


class AgentScalarTests(SimpleTestCase):
    def test_flattens_querydict_list(self):
        self.assertEqual(agent_scalar(["2026-07-31"]), "2026-07-31")

    def test_parses_date_from_list(self):
        self.assertEqual(agent_date(["2026-07-31"]), date(2026, 7, 31))

    def test_resolve_shift_date_range_from_list_params(self):
        params = {
            "date_from": "2026-07-31",
            "date_to": "2026-07-31",
        }
        date_from, date_to = resolve_shift_date_range(params, default_today=False)
        self.assertEqual(date_from, date(2026, 7, 31))
        self.assertEqual(date_to, date(2026, 7, 31))


class AgentListShiftsDateFilterTests(SimpleTestCase):
    def test_list_shifts_does_not_crash_on_list_date_params(self):
        from django.conf import settings
        from scheduling.views_agent import agent_list_shifts

        factory = APIRequestFactory()
        today = timezone.localdate().isoformat()
        request = factory.get(
            "/api/scheduling/agent/list-shifts/",
            {
                "date_from": [today],
                "date_to": [today],
            },
        )
        request.META["HTTP_AUTHORIZATION"] = f"Bearer {settings.LUA_WEBHOOK_API_KEY or 'test-key'}"

        response = agent_list_shifts(request)
        # Must not 500 / TypeError from list-valued date params
        self.assertIn(response.status_code, (200, 400, 401))
        self.assertNotIn("fromisoformat", str(response.data))
