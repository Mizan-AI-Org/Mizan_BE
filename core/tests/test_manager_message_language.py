"""Manager→staff WhatsApp language alignment (no FR shell + EN body)."""

from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from core.i18n import (
    detect_message_language,
    format_manager_whatsapp_freeform,
    resolve_manager_message_language,
)
from notifications.services import NotificationService


class ManagerMessageLanguageTests(SimpleTestCase):
    def test_detect_english_message(self):
        self.assertEqual(
            detect_message_language("Adama, please prepare the buffet."),
            "en",
        )

    def test_detect_french_message(self):
        self.assertEqual(
            detect_message_language("Adama, prépare le buffet s'il te plaît."),
            "fr",
        )

    def test_resolve_prefers_message_language(self):
        lang = resolve_manager_message_language(
            message="Please prepare the buffet.",
            fallback="fr",
        )
        self.assertEqual(lang, "en")

    def test_format_freeform_english(self):
        text = format_manager_whatsapp_freeform(
            "Adama, please prepare the buffet.",
            lang="en",
        )
        self.assertIn("Message from your manager", text)
        self.assertIn("please prepare the buffet", text)
        self.assertNotIn("responsable", text.lower())

    @override_settings(
        WHATSAPP_TEMPLATE_MANAGER_MESSAGE="manager_message",
        WHATSAPP_TEMPLATE_MANAGER_MESSAGE_LANGUAGE="fr",
    )
    @patch.object(NotificationService, "send_whatsapp_template")
    def test_template_uses_english_for_english_body(self, mock_send):
        mock_send.return_value = (True, {"status_code": 200})
        svc = NotificationService()
        ok, _ = svc._send_manager_message_template(
            "2203736808",
            "Adama, please prepare the buffet.",
            audit=False,
            allow_text_fallback=False,
        )
        self.assertTrue(ok)
        _args, kwargs = mock_send.call_args
        self.assertEqual(kwargs["language_code"], "en_US")
        self.assertEqual(
            kwargs["components"][0]["parameters"][0]["text"],
            "Adama, please prepare the buffet.",
        )
        self.assertEqual(kwargs["fallback_context"]["language"], "en")
