"""Regression: checklist intents must not hit dashboard.Task handler."""

from unittest.mock import MagicMock

from django.test import SimpleTestCase

from notifications.dashboard_task_whatsapp import (
    handle_dashboard_task_whatsapp_reply,
    looks_like_dashboard_task_status_reply,
)


class DashboardTaskWhatsappRoutingTests(SimpleTestCase):
    def test_start_checklist_not_dashboard_task_reply(self):
        self.assertFalse(looks_like_dashboard_task_status_reply("start checklist"))

    def test_handle_skips_start_checklist(self):
        svc = MagicMock()
        handled = handle_dashboard_task_whatsapp_reply(
            notification_service=svc,
            user=MagicMock(),
            phone_digits="212600000001",
            text_body="start checklist",
        )
        self.assertFalse(handled)
        svc.send_whatsapp_text.assert_not_called()
