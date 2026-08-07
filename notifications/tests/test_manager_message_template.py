"""manager_message uses named {{message}} — positional params break Meta."""

from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from notifications.services import NotificationService


class ManagerMessageTemplateTests(SimpleTestCase):
    @override_settings(
        WHATSAPP_TEMPLATE_MANAGER_MESSAGE="manager_message",
        WHATSAPP_TEMPLATE_MANAGER_MESSAGE_LANGUAGE="fr",
    )
    @patch.object(NotificationService, "send_whatsapp_template")
    def test_uses_named_message_parameter(self, mock_send):
        mock_send.return_value = (True, {"status_code": 200})
        svc = NotificationService()
        ok, _ = svc._send_manager_message_template(
            "2203736808",
            "Please come in",
            audit=False,
            allow_text_fallback=False,
        )
        self.assertTrue(ok)
        _args, kwargs = mock_send.call_args
        components = kwargs["components"]
        params = components[0]["parameters"]
        self.assertEqual(params[0]["parameter_name"], "message")
        self.assertEqual(params[0]["text"], "Please come in")
        self.assertEqual(kwargs["language_code"], "en_US")
        self.assertIs(kwargs["allow_text_fallback"], False)
