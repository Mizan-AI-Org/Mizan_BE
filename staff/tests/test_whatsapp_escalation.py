"""Tests for staff WhatsApp escalation detection."""

from django.test import SimpleTestCase

from staff.whatsapp_escalation import (
    classify_whatsapp_escalation,
    is_confirm_send_reply,
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
