"""Operations Live sort order: critical first, then newest."""
from __future__ import annotations

from django.test import SimpleTestCase

from dashboard.api.operations_live import _sort_open


class OperationsLiveSortTests(SimpleTestCase):
    def test_critical_first_then_newest(self):
        rows = [
            {
                "operation": "Old pending",
                "display_status": "pending",
                "created_at": "2026-08-01T10:00:00+00:00",
            },
            {
                "operation": "Critical meeting",
                "display_status": "critical",
                "created_at": "2026-08-05T10:00:00+00:00",
            },
            {
                "operation": "Yesterday payslip",
                "display_status": "pending",
                "created_at": "2026-08-06T10:00:00+00:00",
            },
        ]
        ordered = sorted(rows, key=_sort_open)
        self.assertEqual(ordered[0]["operation"], "Critical meeting")
        self.assertEqual(ordered[1]["operation"], "Yesterday payslip")
        self.assertEqual(ordered[2]["operation"], "Old pending")

    def test_newest_among_non_critical(self):
        rows = [
            {
                "operation": "Week old",
                "display_status": "pending",
                "created_at": "2026-07-30T10:00:00+00:00",
            },
            {
                "operation": "Yesterday",
                "display_status": "pending",
                "created_at": "2026-08-06T10:00:00+00:00",
            },
        ]
        ordered = sorted(rows, key=_sort_open)
        self.assertEqual(ordered[0]["operation"], "Yesterday")
