from django.test import SimpleTestCase, override_settings

from core.whatsapp_config import (
    clean_whatsapp_env_value,
    is_whatsapp_platform_auth_error,
    parse_whatsapp_api_error,
    resolve_whatsapp_access_token,
    user_facing_whatsapp_error,
)


class WhatsAppConfigTests(SimpleTestCase):
    def test_clean_whatsapp_env_value_strips_quotes_and_whitespace(self):
        raw = '  "EAAabc def\nghi"  '
        self.assertEqual(clean_whatsapp_env_value(raw), "EAAabcdefghi")

    def test_resolve_whatsapp_access_token_plain(self):
        with override_settings(WHATSAPP_ACCESS_TOKEN=" EAA1234567890 "):
            self.assertEqual(resolve_whatsapp_access_token(), "EAA1234567890")

    def test_parse_whatsapp_api_error_json(self):
        payload = {
            "error": {
                "message": "The access token could not be decrypted",
                "type": "OAuthException",
                "code": 190,
            }
        }
        self.assertIn("could not be decrypted", parse_whatsapp_api_error(payload))

    def test_platform_auth_error_detection(self):
        self.assertTrue(
            is_whatsapp_platform_auth_error(
                "The access token could not be decrypted (#190)"
            )
        )

    def test_user_facing_whatsapp_error_masks_token_issue(self):
        msg = user_facing_whatsapp_error(
            "The access token could not be decrypted"
        )
        self.assertIn("temporarily unavailable", msg.lower())
        self.assertNotIn("access token", msg.lower())
