"""Phase 6 — Proactive Operational Intelligence tests."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.utils import timezone

from miya.services.intelligence.proactive.briefing import (
    category_from_handle_phrase,
    format_daily_briefing,
)
from miya.services.intelligence.proactive.dedupe import (
    compute_fingerprint,
    should_send_briefing,
)
from miya.services.intelligence.proactive.prefs import in_quiet_hours
from miya.services.intelligence.proactive.types import (
    AttentionCategory,
    AttentionItem,
    DailyBriefing,
    Severity,
)


def _briefing(*items: AttentionItem) -> DailyBriefing:
    b = DailyBriefing(
        restaurant_id="r1",
        restaurant_name="Demo",
        period="morning",
        items=list(items),
        generated_at=timezone.now().isoformat(),
    )
    b.fingerprint = compute_fingerprint(b)
    return b


class FormatBriefingTests(SimpleTestCase):
    def test_example_shape(self):
        b = _briefing(
            AttentionItem(
                category=AttentionCategory.OPEN_INCIDENTS,
                severity=Severity.CRITICAL,
                title="2 unresolved incidents",
                count=2,
                handle_hint="incidents",
            ),
            AttentionItem(
                category=AttentionCategory.OVERDUE_TASKS,
                severity=Severity.HIGH,
                title="4 overdue tasks",
                count=4,
                handle_hint="tasks",
            ),
            AttentionItem(
                category=AttentionCategory.PENDING_APPROVALS,
                severity=Severity.MEDIUM,
                title="2 invoices awaiting approval",
                count=2,
                entity_ids=["i1", "i2"],
                handle_hint="invoices",
            ),
            AttentionItem(
                category=AttentionCategory.EXPIRING_DOCUMENTS,
                severity=Severity.LOW,
                title="Insurance expires in 21 days",
                count=1,
                handle_hint="insurance",
            ),
            AttentionItem(
                category=AttentionCategory.UNCOMPLETED_CHECKLISTS,
                severity=Severity.MEDIUM,
                title="3 staff haven't completed their opening checklist",
                count=3,
                handle_hint="checklists",
            ),
            AttentionItem(
                category=AttentionCategory.UPCOMING_MEETINGS,
                severity=Severity.INFO,
                title="Kitchen meeting at 10:00",
                count=1,
                actionable=False,
            ),
        )
        text = format_daily_briefing(b, manager_name="Sara")
        self.assertIn("Good morning, Sara.", text)
        self.assertIn("Here's what needs your attention today:", text)
        self.assertIn("🔴 2 unresolved incidents", text)
        self.assertIn("🟠 4 overdue tasks", text)
        self.assertIn("💰 2 invoices awaiting approval", text)
        self.assertIn("📄 Insurance expires in 21 days", text)
        self.assertIn("👥 3 staff haven't completed their opening checklist", text)
        self.assertIn("📅 Kitchen meeting at 10:00", text)
        self.assertIn("Want me to handle any of these?", text)


class HandlePhraseTests(SimpleTestCase):
    def test_handle_the_invoices(self):
        self.assertEqual(
            category_from_handle_phrase("Handle the invoices."),
            AttentionCategory.PENDING_APPROVALS,
        )

    def test_handle_incidents(self):
        self.assertEqual(
            category_from_handle_phrase("handle the incidents"),
            AttentionCategory.OPEN_INCIDENTS,
        )


class DedupeTests(SimpleTestCase):
    @patch("miya.services.intelligence.proactive.dedupe.cache")
    def test_no_resend_same_state_same_day(self, cache):
        b = _briefing(
            AttentionItem(
                category=AttentionCategory.OVERDUE_TASKS,
                severity=Severity.HIGH,
                title="1 overdue task",
                count=1,
                entity_ids=["t1"],
            )
        )
        cache.get.side_effect = lambda key: b.fingerprint if "daily_ops_intel:" in key or "fp" in key else None
        # First call: day key set → already sent, same fp → suppress
        cache.get.side_effect = lambda key: (
            b.fingerprint if "daily_ops_intel:" in key or "daily_ops_intel_fp" in key else None
        )
        allow, reason = should_send_briefing(b, user_id="u1")
        self.assertFalse(allow)
        self.assertIn(reason, ("duplicate_same_state", "already_sent_today_no_escalation"))

    @patch("miya.services.intelligence.proactive.dedupe.cache")
    def test_empty_briefing_not_sent(self, cache):
        cache.get.return_value = None
        b = _briefing()
        allow, reason = should_send_briefing(b, user_id="u1")
        self.assertFalse(allow)
        self.assertEqual(reason, "nothing_needs_attention")

    @patch("miya.services.intelligence.proactive.dedupe.cache")
    def test_first_morning_send(self, cache):
        cache.get.return_value = None
        b = _briefing(
            AttentionItem(
                category=AttentionCategory.OPEN_INCIDENTS,
                severity=Severity.CRITICAL,
                title="1 unresolved incident",
                count=1,
                entity_ids=["x"],
            )
        )
        allow, reason = should_send_briefing(b, user_id="u1")
        self.assertTrue(allow)
        self.assertEqual(reason, "morning_or_first")


class QuietHoursTests(SimpleTestCase):
    def test_quiet_hours_blocks(self):
        user = MagicMock()
        prefs = MagicMock()
        prefs.quiet_hours_enabled = True
        from datetime import time as dtime

        prefs.quiet_hours_start = dtime(22, 0)
        prefs.quiet_hours_end = dtime(7, 0)
        with patch(
            "miya.services.intelligence.proactive.prefs.load_notification_prefs",
            return_value=prefs,
        ):
            now = timezone.now().replace(hour=23, minute=0, second=0, microsecond=0)
            self.assertTrue(in_quiet_hours(user, now=now))
            midday = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)
            self.assertFalse(in_quiet_hours(user, now=midday))


class HandleWorkflowTests(SimpleTestCase):
    def test_handle_invoices_starts_submit(self):
        from miya.services.intelligence.proactive.handle import try_handle_briefing_request
        from miya.services.ops.result import ok

        user = MagicMock()
        user.id = "u1"
        rest = MagicMock()
        rest.id = "r1"
        user.restaurant = rest

        brief = _briefing(
            AttentionItem(
                category=AttentionCategory.PENDING_APPROVALS,
                severity=Severity.MEDIUM,
                title="2 invoices awaiting approval",
                count=2,
                entity_ids=["inv-1", "inv-2"],
                handle_hint="invoices",
            )
        )
        with (
            patch(
                "miya.services.intelligence.proactive.handle.load_briefing_context",
                return_value=brief,
            ),
            patch(
                "miya.services.intelligence.proactive.handle.ops_context_for_channel",
                return_value=MagicMock(restaurant=rest, user=user),
            ),
            patch(
                "miya.services.intelligence.proactive.handle.execute_structured_action",
                return_value=ok(
                    message="Approval requested for Acme.",
                    verified=True,
                    data={"invoice": {"id": "inv-1"}},
                ),
            ) as esa,
        ):
            out = try_handle_briefing_request(
                user=user,
                message="Handle the invoices.",
                channel="whatsapp",
                restaurant=rest,
            )
        self.assertIsNotNone(out)
        self.assertTrue(out.get("success"))
        self.assertEqual(out.get("proactive_handle"), "pending_approvals")
        self.assertEqual(esa.call_count, 2)
        self.assertEqual(esa.call_args_list[0][0][0], "submit_invoice")


class ScannerSmokeTests(SimpleTestCase):
    def test_scan_aggregates_mocked_domains(self):
        from miya.services.intelligence.proactive.scanner import scan_daily_operations

        rest = MagicMock()
        rest.id = "r1"
        rest.name = "Demo"
        user = MagicMock()
        user.id = "u1"

        with (
            patch(
                "miya.services.intelligence.proactive.scanner._scan_incidents",
                return_value=[
                    AttentionItem(
                        category=AttentionCategory.OPEN_INCIDENTS,
                        severity=Severity.CRITICAL,
                        title="2 unresolved incidents",
                        count=2,
                    )
                ],
            ),
            patch(
                "miya.services.intelligence.proactive.scanner._scan_overdue_and_blocked_tasks",
                return_value=[],
            ),
            patch(
                "miya.services.intelligence.proactive.scanner._scan_pending_approvals",
                return_value=[],
            ),
            patch(
                "miya.services.intelligence.proactive.scanner._scan_payment_issues",
                return_value=[],
            ),
            patch(
                "miya.services.intelligence.proactive.scanner._scan_expiring_documents",
                return_value=[],
            ),
            patch(
                "miya.services.intelligence.proactive.scanner._scan_meetings",
                return_value=[],
            ),
            patch(
                "miya.services.intelligence.proactive.scanner._scan_checklists",
                return_value=[],
            ),
            patch(
                "miya.services.intelligence.proactive.scanner._scan_staff_issues",
                return_value=[],
            ),
        ):
            b = scan_daily_operations(rest, user=user)
        self.assertEqual(len(b.items), 1)
        self.assertTrue(b.fingerprint)
