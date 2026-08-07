"""Tests for Google Calendar ↔ WhatsApp reminder sync."""
from __future__ import annotations

from django.test import SimpleTestCase

from scheduling.calendar_reminder_sync import (
    build_meeting_approach_message,
    gcal_event_id_from_body,
)


class CalendarReminderSyncTests(SimpleTestCase):
    def test_gcal_event_id_from_body(self):
        body = "gcal_event_id:abc123xyz\nMeeting on your Google Calendar."
        self.assertEqual(gcal_event_id_from_body(body), "abc123xyz")

    def test_build_meeting_approach_message(self):
        from django.utils import timezone
        from datetime import datetime
        from zoneinfo import ZoneInfo

        start = datetime(2026, 8, 8, 13, 0, tzinfo=ZoneInfo("UTC"))
        msg = build_meeting_approach_message(
            title="Insurance review",
            start_at=start,
            minutes_before=30,
        )
        self.assertIn("Insurance review", msg)
        self.assertIn("30 minutes", msg)
