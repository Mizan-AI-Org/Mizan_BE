"""Proactive Operations Live manager briefings."""

from django.test import SimpleTestCase
from django.utils import timezone

from dashboard.api.operations_live import (
    format_operations_live_briefing,
    format_operations_live_summary,
    _row_completed_today,
)


class OperationsLiveBriefingFormatTests(SimpleTestCase):
    def _payload(self, **overrides):
        base = {
            "restaurant_name": "Barometre",
            "counts": {"pending": 1, "in_progress": 1, "completed": 1},
            "pending": [
                {
                    "operation": "Stage Setup",
                    "category": "Wedding",
                    "display_status": "critical",
                    "to": {"name": "Ahmed Hassan"},
                    "age_label": "6d ago",
                },
            ],
            "in_progress": [
                {
                    "operation": "Decoration",
                    "category": "Wedding",
                    "display_status": "in progress",
                    "to": {"name": "Me"},
                    "age_label": "6d ago",
                },
            ],
            "completed": [],
        }
        base.update(overrides)
        return base

    def test_morning_brief_header(self):
        text = format_operations_live_briefing(self._payload(), period="morning")
        self.assertIn("Good morning", text)
        self.assertIn("Miya ops brief", text)
        self.assertIn("Stage Setup", text)
        self.assertIn("Decoration", text)
        self.assertIn("critical", text.lower())

    def test_evening_debrief_includes_completed_today(self):
        today_iso = timezone.now().isoformat()
        payload = self._payload(
            completed=[
                {
                    "operation": "Seating Arrangement",
                    "category": "Wedding",
                    "updated_at": today_iso,
                },
            ],
        )
        text = format_operations_live_briefing(payload, period="evening")
        self.assertIn("Evening debrief", text)
        self.assertIn("Wrapped up today", text)
        self.assertIn("Seating Arrangement", text)

    def test_row_completed_today(self):
        today = timezone.localdate()
        self.assertTrue(
            _row_completed_today({"updated_at": timezone.now().isoformat()}, today)
        )
        self.assertFalse(_row_completed_today({"updated_at": "2020-01-01T10:00:00Z"}, today))

    def test_all_clear_morning(self):
        payload = {
            "restaurant_name": "Barometre",
            "counts": {"pending": 0, "in_progress": 0, "completed": 0},
            "pending": [],
            "in_progress": [],
        }
        text = format_operations_live_briefing(payload, period="morning")
        self.assertIn("all clear", text.lower())
