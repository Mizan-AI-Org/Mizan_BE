"""Tests for staff WhatsApp escalation detection."""

from django.test import SimpleTestCase

from staff.whatsapp_escalation import (
    classify_whatsapp_escalation,
    extract_escalation_text_from_whatsapp_message,
    is_confirm_send_reply,
    is_explicit_confirm_send_reply,
    looks_like_staff_manager_escalation,
)


class WhatsAppEscalationTests(SimpleTestCase):
    def test_wages_tell_manager_classifies_payroll(self):
        msg = "Tell my manager that I'm yet to receive my last week wages"
        routed = classify_whatsapp_escalation(msg)
        self.assertIsNotNone(routed)
        assert routed is not None
        self.assertEqual(routed["category"], "PAYROLL")
        self.assertTrue(looks_like_staff_manager_escalation(msg))

    def test_confirm_button_reply(self):
        self.assertTrue(is_confirm_send_reply("Yes, send it"))
        self.assertTrue(is_explicit_confirm_send_reply("Yes, send it"))
        self.assertTrue(is_confirm_send_reply("Yes"))
        self.assertFalse(is_explicit_confirm_send_reply("Yes"))

    def test_extract_from_quoted_you_block(self):
        body = (
            "I'm preparing to let your manager know…\n"
            "You: Tell my manager that I'm yet to receive my last week wages"
        )
        candidates = extract_escalation_text_from_whatsapp_message(None, body)
        self.assertTrue(
            any(classify_whatsapp_escalation(c) for c in candidates),
            candidates,
        )

    def test_confirm_recovers_quoted_wages_from_context(self):
        msg = {
            "type": "interactive",
            "interactive": {
                "type": "button_reply",
                "button_reply": {"id": "yes_send", "title": "Yes, send it"},
            },
            "context": {
                "quoted_message": {
                    "body": (
                        "Please confirm if 'manager' is the correct recipient.\n"
                        "You: Tell my manager that I'm yet to receive my last week wages"
                    ),
                },
            },
        }
        candidates = extract_escalation_text_from_whatsapp_message(msg, "Yes, send it")
        routed = None
        for c in candidates:
            if is_confirm_send_reply(c):
                continue
            routed = classify_whatsapp_escalation(c)
            if routed:
                break
        self.assertIsNotNone(routed)
        assert routed is not None
        self.assertEqual(routed["category"], "PAYROLL")
