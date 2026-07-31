"""Tests for Miya reply formatting."""

from django.test import SimpleTestCase

from miya.services.reply_format import format_miya_reply


class FormatMiyaReplyTests(SimpleTestCase):
    def test_strips_bold_and_em_dash(self):
        raw = "**Insurance Reminder**: due on **August 7, 2026** — all set!"
        out = format_miya_reply(raw)
        self.assertNotIn("**", out)
        self.assertNotIn("—", out)
        self.assertIn("Insurance Reminder", out)
        self.assertIn("August 7, 2026", out)

    def test_strips_bullet_prefix(self):
        raw = "- First item\n- Second item"
        out = format_miya_reply(raw)
        self.assertNotIn("- First", out)
        self.assertIn("First item", out)
