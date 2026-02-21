"""
Tests for clock-in location request flow: Share Location button only, no plain-text fallback.
"""
from unittest.mock import patch, MagicMock

from django.test import SimpleTestCase, override_settings

from notifications.services import NotificationService


@override_settings(
    WHATSAPP_ACCESS_TOKEN='test_token',
    WHATSAPP_PHONE_NUMBER_ID='test_phone_id',
    WHATSAPP_TEMPLATE_CLOCK_IN_LOCATION='clock_in_location_request',
)
class SendWhatsAppLocationRequestTests(SimpleTestCase):
    """Ensure location request always uses template or interactive (Share Location button), never plain text."""

    def test_template_success_returns_ok_and_does_not_call_interactive_or_text(self):
        service = NotificationService()
        with patch.object(service, 'send_whatsapp_template', return_value=(True, {"id": "wamid.1"})) as mock_tpl:
            with patch.object(service, 'send_whatsapp_location_request_interactive') as mock_interactive:
                with patch.object(service, 'send_whatsapp_text') as mock_text:
                    ok, resp = service.send_whatsapp_location_request('15551234567', 'Please share your live location to clock in.')
        self.assertTrue(ok)
        mock_tpl.assert_called_once()
        mock_interactive.assert_not_called()
        mock_text.assert_not_called()

    def test_template_fails_interactive_succeeds_returns_ok_and_does_not_call_text(self):
        service = NotificationService()
        with patch.object(service, 'send_whatsapp_template', return_value=(False, {})):
            with patch.object(service, 'send_whatsapp_location_request_interactive', return_value=(True, {"id": "wamid.2"})) as mock_interactive:
                with patch.object(service, 'send_whatsapp_text') as mock_text:
                    ok, resp = service.send_whatsapp_location_request('15551234567', None)
        self.assertTrue(ok)
        self.assertGreaterEqual(mock_interactive.call_count, 1)
        mock_text.assert_not_called()

    def test_both_fail_returns_false_and_never_calls_send_whatsapp_text(self):
        service = NotificationService()
        with patch.object(service, 'send_whatsapp_template', return_value=(False, {})):
            with patch.object(service, 'send_whatsapp_location_request_interactive', return_value=(False, {"error": "rate limit"})):
                with patch.object(service, 'send_whatsapp_text') as mock_text:
                    ok, resp = service.send_whatsapp_location_request('15551234567', 'Fallback body')
        self.assertFalse(ok)
        self.assertIn('error', resp)
        mock_text.assert_not_called()

    def test_interactive_payload_has_location_request_message_type(self):
        """Interactive location request must use type location_request_message and action send_location."""
        service = NotificationService()
        with patch('notifications.services.requests.post', return_value=MagicMock(status_code=200, json=lambda: {"messages": [{"id": "wamid.1"}]})) as mock_post:
            with patch.object(service, 'send_whatsapp_template', return_value=(False, {})):
                ok, _ = service.send_whatsapp_location_request('15551234567', 'Please share your live location to clock in.')
        self.assertTrue(ok)
        self.assertEqual(mock_post.call_count, 1)
        payload = mock_post.call_args.kwargs.get('json') or mock_post.call_args[1].get('json')
        self.assertIsNotNone(payload)
        self.assertEqual(payload.get('type'), 'interactive')
        interactive = payload.get('interactive', {})
        self.assertEqual(interactive.get('type'), 'location_request_message')
        self.assertEqual(interactive.get('action', {}).get('name'), 'send_location')
        self.assertIn('body', interactive)
        self.assertIn('text', interactive['body'])
