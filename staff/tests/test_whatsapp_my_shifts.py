"""Tests for WhatsApp my-shifts detection."""

from django.test import SimpleTestCase

from staff.whatsapp_my_shifts import looks_like_my_shifts_query, _parse_shift_range


class WhatsAppMyShiftsTests(SimpleTestCase):
    def test_today_and_tomorrow_query(self):
        msg = "Hello Miya, when is my shift today and tomorrow?"
        self.assertTrue(looks_like_my_shifts_query(msg))
        start, end = _parse_shift_range(msg)
        self.assertEqual((end - start).days, 1)

    def test_clock_me_in_not_shifts(self):
        self.assertFalse(looks_like_my_shifts_query("Clock me in"))
