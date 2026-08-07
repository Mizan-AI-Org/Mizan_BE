"""Phase 11 Option C — operational audit trail + history routing."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from miya.services.intelligence.copilot.understand import (
    is_briefing_query,
    is_operational_search_query,
    understand_turn,
)
from miya.services.intelligence.search.classify_query import parse_search_query
from miya.services.intelligence.search.types import SearchDomain, SearchMode


class BriefingVsHistoryRoutingTests(SimpleTestCase):
    def test_maxime_photos_not_briefing(self):
        msg = "What happened to Maxime's photos?"
        self.assertFalse(is_briefing_query(msg))
        c = understand_turn(msg)
        self.assertTrue(is_operational_search_query(msg, c))

    def test_what_happened_today_still_search_capable(self):
        msg = "What happened today?"
        c = understand_turn(msg)
        self.assertTrue(is_operational_search_query(msg, c))

    def test_daily_briefing_still_briefing(self):
        msg = "What needs my attention today?"
        self.assertTrue(is_briefing_query(msg))

    def test_status_question_not_briefing(self):
        msg = "What is the status of Maxime's photos?"
        self.assertFalse(is_briefing_query(msg))
        c = understand_turn(msg)
        self.assertTrue(is_operational_search_query(msg, c))

    def test_who_changed_routes_search(self):
        msg = "Who changed Maxime's photos task?"
        self.assertFalse(is_briefing_query(msg))
        c = understand_turn(msg)
        self.assertTrue(is_operational_search_query(msg, c))

    def test_when_completed_routes_search(self):
        msg = "When was Maxime's photos task completed?"
        self.assertFalse(is_briefing_query(msg))
        c = understand_turn(msg)
        self.assertTrue(is_operational_search_query(msg, c))


class SearchClassificationTests(SimpleTestCase):
    def test_maxime_photos_classified_task(self):
        parsed = parse_search_query("What happened to Maxime's photos?")
        self.assertEqual(parsed.domain, SearchDomain.TASK)
        self.assertIn(parsed.mode, (SearchMode.EVENT, SearchMode.HYBRID))


class OperationalAuditServiceTests(SimpleTestCase):
    @patch("miya.services.intelligence.operational_memory.record_operational_observation")
    @patch("miya.models.OperationalEvent")
    def test_idempotent_duplicate_skips_create(self, EventModel, mock_record):
        from core.operational_audit.service import record_operational_audit_event

        existing = MagicMock()
        existing.id = "ev-1"
        existing.event_type = "TASK_COMPLETED"
        existing.entity_type = "task"
        existing.entity_id = "22222222-2222-2222-2222-222222222222"
        existing.created_at = None
        EventModel.objects.filter.return_value.first.return_value = existing

        row = record_operational_audit_event(
            restaurant=MagicMock(id="11111111-1111-1111-1111-111111111111"),
            event_type="TASK_COMPLETED",
            entity_type="task",
            entity_id="22222222-2222-2222-2222-222222222222",
            operation_id="op-1",
            summary="Done",
        )
        self.assertTrue(row.get("deduplicated"))
        mock_record.assert_not_called()

    @patch("miya.services.intelligence.operational_memory.record_operational_observation")
    @patch("miya.models.OperationalEvent")
    def test_emits_with_previous_and_new_state(self, EventModel, mock_record):
        from core.operational_audit.service import record_operational_audit_event

        EventModel.objects.filter.return_value.first.return_value = None
        mock_record.return_value = {"id": "ev-2", "event_type": "TASK_STATUS_CHANGED"}

        record_operational_audit_event(
            restaurant=MagicMock(id="11111111-1111-1111-1111-111111111111"),
            event_type="TASK_STATUS_CHANGED",
            entity_type="task",
            entity_id="22222222-2222-2222-2222-222222222222",
            operation_id="op-2",
            previous_state={"status": "PENDING"},
            new_state={"status": "IN_PROGRESS"},
            channel="dashboard",
        )
        mock_record.assert_called_once()
        payload = mock_record.call_args.kwargs.get("payload") or {}
        self.assertEqual(payload.get("previous_state"), {"status": "PENDING"})
        self.assertEqual(payload.get("new_state"), {"status": "IN_PROGRESS"})


class GetEntityHistoryTests(SimpleTestCase):
    def _ctx(self, *, role="MANAGER"):
        from miya.services.ops.context import OpsContext

        user = MagicMock()
        user.id = "u1"
        user.role = role
        rest = MagicMock()
        rest.id = "org-1"
        return OpsContext(
            user=user,
            restaurant=rest,
            restaurant_id="org-1",
            user_id="u1",
            role=role,
            channel="dashboard",
            language="en",
        )

    @patch("miya.services.intelligence.operational_memory.reconstruct_entity_timeline")
    def test_get_entity_history_returns_timeline(self, mock_timeline):
        from miya.services.ops.history import get_entity_history
        from miya.services.ops.result import ok

        mock_timeline.return_value = ok(
            message="Timeline",
            verified=True,
            data={
                "entity_type": "task",
                "entity_id": "11111111-1111-1111-1111-111111111111",
                "current": {"id": "11111111-1111-1111-1111-111111111111", "title": "Maxime photos", "status": "COMPLETED"},
                "events": [{"event_type": "TASK_COMPLETED", "summary": "Done"}],
                "timeline": [],
            },
        )
        result = get_entity_history(self._ctx(), q="Maxime photos")
        self.assertTrue(result.success)
        self.assertEqual((result.data or {}).get("layer"), "CANONICAL_ENTITY_HISTORY")
        self.assertEqual(len((result.data or {}).get("history") or []), 1)

    @patch("miya.services.ops.history.get_entity_history")
    def test_current_state_distinct_from_history(self, mock_hist):
        from miya.services.ops.history import get_current_entity_state
        from miya.services.ops.result import ok

        mock_hist.return_value = ok(
            message="ok",
            verified=True,
            data={
                "current_state": {"id": "11111111-1111-1111-1111-111111111111", "status": "IN_PROGRESS"},
                "entity_type": "task",
                "entity_id": "11111111-1111-1111-1111-111111111111",
            },
        )
        result = get_current_entity_state(self._ctx(), q="Maxime photos")
        self.assertTrue(result.success)
        self.assertEqual((result.data or {}).get("layer"), "CURRENT_DATABASE_STATE")
        self.assertNotIn("history", result.data or {})


class TaskAuditEmissionTests(SimpleTestCase):
    @patch("core.operational_audit.service.record_operational_audit_event")
    @patch("dashboard.models.Task")
    def test_update_task_status_emits_audit(self, TaskModel, mock_audit):
        from miya.services.ops.tasks import update_task_status

        task = MagicMock()
        task.id = "11111111-1111-1111-1111-111111111111"
        task.status = "PENDING"
        task.title = "Maxime photos"
        task.restaurant_id = "org-1"
        task.assigned_to_id = "u1"
        task.assignees.filter.return_value.exists.return_value = True

        fresh = MagicMock()
        fresh.id = task.id
        fresh.status = "COMPLETED"
        fresh.title = "Maxime photos"
        TaskModel.objects.select_related.return_value.filter.return_value.first.return_value = fresh

        ctx = MagicMock()
        ctx.restaurant = MagicMock(id="org-1")
        ctx.restaurant_id = "org-1"
        ctx.user = MagicMock(id="u1", pk="u1")
        ctx.user_id = "u1"
        ctx.location_id = None
        ctx.channel = "whatsapp"

        with patch("miya.services.ops.tasks._resolve_task", return_value=(task, None)), patch(
            "miya.services.ops.tasks.require_task_status_permission", return_value=None
        ), patch("miya.services.ops.tasks._serialize_task", return_value={
            "title": "Maxime photos",
            "task_ref": "#ABC123",
            "status": "COMPLETED",
        }), patch(
            "miya.services.message_pipeline.claim_mutation_once", return_value=True
        ):
            result = update_task_status(
                ctx,
                status="COMPLETED",
                task_id=str(task.id),
                skip_idempotency=True,
            )
        self.assertTrue(result.success)
        self.assertTrue((result.data or {}).get("audit_emitted"))
        mock_audit.assert_called_once()


class CrossChannelIdempotencyTests(SimpleTestCase):
    @patch("miya.services.intelligence.actions.emit_ops_event")
    @patch("miya.services.intelligence.actions._handle_update_task_status")
    def test_domain_audit_skips_duplicate_miya_emit(self, mock_handler, mock_emit):
        from miya.services.intelligence.actions import execute_structured_action
        from miya.services.ops.context import OpsContext
        from miya.services.ops.result import ok

        user = MagicMock(id="u1", pk="u1", role="MANAGER")
        rest = MagicMock(id="r1")
        ctx = OpsContext(
            user=user,
            restaurant=rest,
            restaurant_id="r1",
            user_id="u1",
            role="MANAGER",
            channel="dashboard",
            language="en",
        )
        mock_handler.return_value = ok(
            message="ok",
            verified=True,
            data={"audit_emitted": True, "task": {"id": "11111111-1111-1111-1111-111111111111"}},
        )
        with patch("miya.services.intelligence.actions.claim_operation_once", return_value=True):
            execute_structured_action(
                "update_task_status",
                {"status": "COMPLETED", "task_id": "11111111-1111-1111-1111-111111111111"},
                ctx=ctx,
            )
        mock_emit.assert_not_called()


class EventSearchEntityHistoryTests(SimpleTestCase):
    @patch("miya.services.intelligence.operational_memory.recall_operational_memory")
    @patch("miya.services.intelligence.event_history.get_event_history")
    @patch("miya.services.ops.history.get_entity_history")
    def test_event_search_uses_entity_history(self, mock_entity_hist, mock_evt_hist, mock_recall):
        from miya.services.intelligence.search.events import event_search
        from miya.services.intelligence.search.types import ParsedSearchQuery, SearchDomain, SearchMode, SearchFilters
        from miya.services.ops.context import OpsContext
        from miya.services.ops.result import ok, fail

        mock_entity_hist.return_value = ok(
            message="History",
            verified=True,
            data={
                "history": [{"id": "e1", "summary": "Task completed", "event_type": "TASK_COMPLETED"}],
                "current_state": {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "title": "Maxime photos",
                    "status": "COMPLETED",
                },
            },
        )
        mock_evt_hist.return_value = ok(message="events", verified=True, data={"events": []})
        mock_recall.return_value = fail(code="empty", message="empty")

        parsed = ParsedSearchQuery(
            raw="What happened to Maxime's photos?",
            domain=SearchDomain.TASK,
            mode=SearchMode.EVENT,
            filters=SearchFilters(q="Maxime photos"),
        )
        user = MagicMock(id="u1", role="MANAGER")
        rest = MagicMock(id="11111111-1111-1111-1111-111111111111")
        ctx = OpsContext(
            user=user,
            restaurant=rest,
            restaurant_id="11111111-1111-1111-1111-111111111111",
            user_id="u1",
            role="MANAGER",
            channel="dashboard",
            language="en",
        )
        hits = event_search(ctx, parsed)
        self.assertGreaterEqual(len(hits), 1)
        mock_entity_hist.assert_called_once()
