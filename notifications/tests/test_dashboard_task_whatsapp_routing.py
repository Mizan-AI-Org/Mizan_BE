"""Ensure checklist phrases are not routed to dashboard.Task handlers."""

from django.test import SimpleTestCase

from notifications.dashboard_task_whatsapp import looks_like_dashboard_task_status_reply


class DashboardTaskRoutingTests(SimpleTestCase):
    def test_start_checklist_not_dashboard_task(self):
        self.assertFalse(looks_like_dashboard_task_status_reply("Start checklist"))

    def test_bare_yes_not_dashboard_task(self):
        self.assertFalse(looks_like_dashboard_task_status_reply("Yes"))

    def test_done_still_dashboard_task(self):
        self.assertTrue(looks_like_dashboard_task_status_reply("done"))
