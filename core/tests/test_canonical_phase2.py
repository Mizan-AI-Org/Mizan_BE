"""Phase 2 — canonical entity layer tests."""
from __future__ import annotations

from django.test import SimpleTestCase
from unittest.mock import MagicMock, patch

from core.canonical.registry import CANONICAL_ENTITIES, get_canonical_entity
from core.canonical.status import (
    normalize_scheduling_task_status,
    normalize_task_status,
    scheduling_status_from_canonical,
)
from core.canonical.tasks import (
    find_canonical_tasks,
    resolve_canonical_task,
    serialize_canonical_task,
)


class CanonicalRegistryTests(SimpleTestCase):
    def test_all_major_entities_registered(self):
        for name in ("task", "incident", "invoice", "document", "reminder", "meeting", "staff", "establishment"):
            self.assertIsNotNone(get_canonical_entity(name), name)

    def test_task_migration_status_unified_read(self):
        task = CANONICAL_ENTITIES["task"]
        self.assertEqual(task.canonical_model, "dashboard.Task")
        self.assertIn("scheduling.Task", task.legacy_models)
        self.assertEqual(task.migration_status, "unified_read")

    def test_incident_canonical_model(self):
        inc = CANONICAL_ENTITIES["incident"]
        self.assertEqual(inc.canonical_model, "staff.SafetyConcernReport")
        self.assertIn("reporting.Incident", inc.legacy_models)


class CanonicalStatusTests(SimpleTestCase):
    def test_scheduling_todo_maps_to_pending(self):
        self.assertEqual(normalize_scheduling_task_status("TODO"), "PENDING")

    def test_done_alias(self):
        self.assertEqual(normalize_task_status("DONE"), "COMPLETED")

    def test_scheduling_writeback(self):
        self.assertEqual(scheduling_status_from_canonical("PENDING"), "TODO")
        self.assertEqual(scheduling_status_from_canonical("IN_PROGRESS"), "IN_PROGRESS")


class CanonicalTaskSerializeTests(SimpleTestCase):
    def test_dashboard_task_shape(self):
        assignee = MagicMock(id="u1", first_name="Ahmed", last_name="Ben", email="a@test.com")
        task = MagicMock(
            id="11111111-1111-1111-1111-111111111111",
            title="Closing checklist",
            description="",
            status="IN_PROGRESS",
            priority="HIGH",
            category="OPS",
            assigned_to=assignee,
            due_date=None,
            updated_at=None,
        )
        row = serialize_canonical_task(task, origin="dashboard")
        self.assertEqual(row["origin"], "dashboard")
        self.assertEqual(row["status"], "IN_PROGRESS")
        self.assertEqual(row["assignee_name"], "Ahmed Ben")
        self.assertTrue(row["task_ref"].startswith("#"))

    def test_scheduling_task_normalizes_status(self):
        task = MagicMock(
            id="22222222-2222-2222-2222-222222222222",
            title="Shift prep",
            description="",
            status="TODO",
            priority="MEDIUM",
            category=None,
            assigned_to=MagicMock(all=MagicMock(return_value=[])),
            assigned_shift=None,
            due_date=None,
            updated_at=None,
            pk=True,
        )
        row = serialize_canonical_task(task, origin="scheduling")
        self.assertEqual(row["origin"], "scheduling")
        self.assertEqual(row["status"], "PENDING")
        self.assertEqual(row["source_label"], "Scheduling")


class CanonicalTaskResolveTests(SimpleTestCase):
    @patch("core.canonical.tasks._match_dashboard_by_id")
    @patch("core.canonical.tasks._match_scheduling_by_id")
    def test_resolve_by_id_prefers_dashboard(self, mock_sched, mock_dash):
        restaurant = MagicMock()
        dash_task = MagicMock(title="Ops task")
        mock_dash.return_value = dash_task
        mock_sched.return_value = None

        task, origin, meta = resolve_canonical_task(restaurant, task_id="abc123")
        self.assertIs(task, dash_task)
        self.assertEqual(origin, "dashboard")
        self.assertIsNone(meta)

    @patch("core.canonical.tasks._search_dashboard")
    @patch("core.canonical.tasks._search_scheduling")
    def test_resolve_ambiguous_returns_candidates(self, mock_sched_search, mock_dash_search):
        restaurant = MagicMock()
        t1 = MagicMock(
            title="Decoration",
            id="11111111-1111-1111-1111-111111111111",
            description="",
            status="PENDING",
            priority="LOW",
            category="",
            assigned_to=None,
            due_date=None,
            updated_at=None,
        )
        t2 = MagicMock(
            title="Decoration",
            id="22222222-2222-2222-2222-222222222222",
            description="",
            status="TODO",
            priority="LOW",
            category=None,
            assigned_to=MagicMock(all=MagicMock(return_value=[])),
            assigned_shift=None,
            due_date=None,
            updated_at=None,
            pk=True,
        )
        mock_dash_search.return_value = [t1]
        mock_sched_search.return_value = [t2]

        task, origin, meta = resolve_canonical_task(restaurant, q="decoration")
        self.assertIsNone(task)
        self.assertIsInstance(meta, list)
        self.assertGreaterEqual(len(meta), 2)


class MiyaOpsUsesCanonicalTests(SimpleTestCase):
    @patch("core.canonical.tasks.find_canonical_tasks")
    def test_find_tasks_delegates_to_canonical(self, mock_find):
        from miya.services.ops.context import OpsContext
        from miya.services.ops.tasks import find_tasks

        mock_find.return_value = [{"id": "1", "title": "T", "status": "PENDING", "origin": "dashboard"}]
        user = MagicMock(id="u1", role="MANAGER")
        rest = MagicMock(id="r1")
        ctx = OpsContext(
            user=user,
            restaurant=rest,
            restaurant_id="r1",
            user_id="u1",
            role="MANAGER",
            channel="dashboard",
            available_locations=[],
        )
        with patch("miya.services.ops.tasks.require_restaurant", return_value=None), patch(
            "miya.services.ops.tasks.require_permission", return_value=None
        ), patch("miya.services.ops.context.require_establishment_context", return_value=None):
            result = find_tasks(ctx, status="OPEN", limit=5)
        self.assertTrue(result.success)
        mock_find.assert_called_once()
        self.assertEqual(len((result.data or {}).get("tasks") or []), 1)
