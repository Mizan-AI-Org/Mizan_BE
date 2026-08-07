"""Pending Operations Live listing for manager task queries."""
from __future__ import annotations

from django.test import SimpleTestCase

from dashboard.api.operations_live import format_operations_live_summary
from miya.services.agent import _looks_like_pending_ops_query, _reply_from_operations_live_result


class PendingOpsQueryTests(SimpleTestCase):
    def test_detects_pending_tasks_for_today(self):
        self.assertTrue(
            _looks_like_pending_ops_query(
                "what are the pending tasks for today", "MANAGER"
            )
        )

    def test_detects_where_are_we_at_today(self):
        self.assertTrue(
            _looks_like_pending_ops_query("where are we at today?", "MANAGER")
        )
        self.assertTrue(
            _looks_like_pending_ops_query("give me a status update", "OWNER")
        )

    def test_ignores_staff_self_pay(self):
        self.assertFalse(
            _looks_like_pending_ops_query("where is my payslip", "WAITER")
        )

    def test_summary_prioritises_critical_and_in_progress(self):
        payload = {
            "restaurant_name": "Barometre",
            "counts": {"pending": 3, "in_progress": 1},
            "pending": [
                {
                    "operation": "Setup for Ceremony in Backyard",
                    "category": "Wedding",
                    "display_status": "critical",
                    "from": {"name": "Hamza Hadni"},
                    "to": {"name": "Admin User"},
                    "age_label": "6d ago",
                },
                {
                    "operation": "Stage Setup",
                    "category": "Wedding",
                    "display_status": "critical",
                    "to": {"name": "Ahmed Hassan"},
                    "age_label": "6d ago",
                },
                {
                    "operation": "Seating Arrangement",
                    "category": "Wedding",
                    "display_status": "critical",
                    "to": {"name": "Me"},
                    "age_label": "6d ago",
                },
            ],
            "in_progress": [
                {
                    "operation": "Decoration",
                    "category": "Wedding",
                    "display_status": "in progress",
                    "to": {"name": "Me"},
                    "age_label": "6d ago",
                },
            ],
        }
        text = format_operations_live_summary(payload)
        self.assertIn("3 new demands", text)
        self.assertIn("1 in progress", text)
        self.assertIn("3 critical", text)
        self.assertIn("🔴 Critical:", text)
        self.assertIn("Setup for Ceremony in Backyard", text)
        self.assertIn("In progress (1):", text)
        self.assertIn("Decoration", text)

    def test_summary_lists_non_critical_pending(self):
        payload = {
            "restaurant_name": "ZAMA ZAMA",
            "counts": {"pending": 2, "in_progress": 0},
            "pending": [
                {
                    "operation": "Meeting with Front of House Staff",
                    "category": "OPERATIONS",
                    "display_status": "pending",
                    "from": {"name": "Wahabi Driss"},
                    "to": {"name": "Me"},
                    "age_label": "2d ago",
                },
                {
                    "operation": "AC repair needed",
                    "category": "INCIDENT",
                    "display_status": "pending",
                    "from": {"name": "Wahabi Driss"},
                    "to": {"name": "AbdelKarim"},
                    "age_label": "1w ago",
                },
            ],
        }
        text = format_operations_live_summary(payload)
        self.assertIn("2 new demand", text)
        self.assertIn("Other new demands", text)
        self.assertIn("Meeting with Front of House Staff", text)
        self.assertIn("AC repair needed", text)

    def test_reply_from_tool_result_uses_message_for_user(self):
        result = {
            "success": True,
            "message_for_user": (
                "Here's where things stand at Barometre: 3 new demands, 1 in progress."
            ),
        }
        self.assertIn("Barometre", _reply_from_operations_live_result(result))
