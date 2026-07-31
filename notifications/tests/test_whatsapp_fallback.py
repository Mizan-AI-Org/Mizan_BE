"""Tests for intelligent WhatsApp template fallbacks."""

from django.test import SimpleTestCase

from notifications.whatsapp_fallback import (
    compose_intelligent_fallback,
    is_missing_template_error,
)


class WhatsAppFallbackTests(SimpleTestCase):
    def test_detects_missing_template_meta_error(self):
        payload = {
            "error": {
                "message": "(#132001) Template name does not exist in the translation",
                "code": 132001,
            }
        }
        self.assertTrue(is_missing_template_error(payload))

    def test_shift_assigned_fallback(self):
        text = compose_intelligent_fallback(
            "staff_weekly_schedule",
            components=[
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": "Adama"},
                        {"type": "text", "text": "Script R3"},
                        {"type": "text", "text": "Friday, July 31"},
                        {"type": "text", "text": "18:00"},
                        {"type": "text", "text": "23:00"},
                        {"type": "text", "text": "WAITER"},
                    ],
                }
            ],
        )
        self.assertIn("Adama", text)
        self.assertIn("18:00", text)
        self.assertIn("Clock in", text)

    def test_prefers_explicit_fallback_body(self):
        text = compose_intelligent_fallback(
            "anything",
            fallback_body="Custom manager note for the team.",
        )
        self.assertEqual(text, "Custom manager note for the team.")

    def test_dinner_context_from_message(self):
        text = compose_intelligent_fallback(
            "unknown_template",
            fallback_body="",
            context={"message": "Shift assigned for dinner service tonight."},
        )
        self.assertIn("dinner", text.lower())
