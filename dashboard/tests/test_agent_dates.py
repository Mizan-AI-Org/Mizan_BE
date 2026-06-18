from datetime import date

from django.test import SimpleTestCase

from dashboard.api.agent_dates import coerce_agent_date


class CoerceAgentDateTests(SimpleTestCase):
    def test_iso_date(self):
        self.assertEqual(coerce_agent_date("2023-06-25"), date(2023, 6, 25))

    def test_human_readable_date(self):
        self.assertEqual(coerce_agent_date("25 Jun 2023"), date(2023, 6, 25))
        self.assertEqual(coerce_agent_date("June 25, 2023"), date(2023, 6, 25))

    def test_aliases(self):
        self.assertEqual(coerce_agent_date("today"), date.today())

    def test_invalid(self):
        self.assertIsNone(coerce_agent_date("not-a-date"))
