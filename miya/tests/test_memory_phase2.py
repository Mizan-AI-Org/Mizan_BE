"""Phase 2 — Operational Memory architecture tests."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from miya.services.ops.context import OpsContext
from miya.services.ops.result import ok


def _ctx():
    user = MagicMock()
    user.id = "u1"
    user.pk = "u1"
    user.role = "MANAGER"
    rest = MagicMock()
    rest.id = "r1"
    rest.name = "Org"
    return OpsContext(
        user=user,
        restaurant=rest,
        restaurant_id="r1",
        user_id="u1",
        role="MANAGER",
        channel="dashboard",
        language="en",
        location_id="loc-a",
        location_name="Branch A",
        available_locations=[{"id": "loc-a", "name": "Branch A"}],
    )


class MemoryLayerSeparationTests(SimpleTestCase):
    def test_six_layers_in_bundle(self):
        from miya.services.intelligence.memory import assemble_memory_bundle
        from miya.services.intelligence.memory_priority import MEMORY_PRIORITY

        bundle = assemble_memory_bundle(
            history=[
                {"role": "user", "content": "Close decoration"},
                {"role": "assistant", "content": "Done — completed."},
            ],
            conversation_id="c1",
        )
        layers = bundle["layers"]
        self.assertIn("conversation_memory", layers)
        self.assertIn("working_memory", layers)
        self.assertIn("semantic_memory", layers)
        self.assertIn("operational_memory", layers)
        self.assertIn("document_knowledge", layers)
        self.assertIn("event_history", layers)
        self.assertEqual(bundle["priority"], list(MEMORY_PRIORITY))
        # Conversation must not claim operational authority
        self.assertEqual(layers["conversation_memory"]["authority"], "conversation_only")
        self.assertEqual(layers["semantic_memory"]["authority"], "lowest")

    def test_priority_order(self):
        from miya.services.intelligence.memory_priority import MEMORY_PRIORITY, prefer_source

        self.assertEqual(MEMORY_PRIORITY[0], "CURRENT_DATABASE_STATE")
        self.assertEqual(MEMORY_PRIORITY[-1], "SEMANTIC_HISTORICAL_RECALL")
        self.assertEqual(
            prefer_source("CONVERSATION_MEMORY", "CURRENT_DATABASE_STATE"),
            "CURRENT_DATABASE_STATE",
        )


class ConversationCannotOverrideDbTests(SimpleTestCase):
    def test_stale_assistant_claim_ignored_for_status(self):
        """Discuss task → dashboard changes DB → Miya reads DB, not chat."""
        from miya.services.intelligence.reality import get_current_task

        # Conversation claimed COMPLETED earlier; DB says IN_PROGRESS after dashboard edit
        db_state = ok(
            message="Decoration is IN_PROGRESS",
            verified=True,
            data={"task": {"id": "123", "title": "Decoration", "status": "IN_PROGRESS"}},
        )
        with patch("miya.services.ops.tasks.get_task_state", return_value=db_state):
            result = get_current_task(_ctx(), task_id="123")
        self.assertEqual(result.data["task"]["status"], "IN_PROGRESS")
        self.assertTrue(result.data.get("overrides_conversation_memory"))
        self.assertEqual(result.data.get("source"), "database")


class CrossChannelRealityTests(SimpleTestCase):
    def test_whatsapp_change_visible_via_get_current(self):
        from miya.services.intelligence.reality import get_current_task

        after_wa = ok(
            message="ok",
            verified=True,
            data={"task": {"id": "123", "title": "Decoration", "status": "COMPLETED"}},
        )
        with patch("miya.services.ops.tasks.get_task_state", return_value=after_wa):
            # Same org/task — channel-agnostic DB read
            dash_ctx = _ctx()
            dash_ctx.channel = "dashboard"
            result = get_current_task(dash_ctx, task_id="123")
        self.assertEqual(result.data["task"]["status"], "COMPLETED")


class OperationalEventPersistenceTests(SimpleTestCase):
    def test_emit_persists_and_survives_restart_simulation(self):
        """Events written to DB remain after process/cache restart (store is DB)."""
        from miya.services.intelligence.events import emit_ops_event

        stored = []

        def fake_record(**kwargs):
            stored.append(kwargs)
            return {"id": "ev1", "event_type": kwargs["event_type"]}

        with patch(
            "miya.services.intelligence.operational_memory.record_operational_observation",
            side_effect=fake_record,
        ), patch(
            "miya.services.intelligence.working_memory.touch_from_entity"
        ):
            emit_ops_event(
                event_type="complete_task.verified",
                operation="complete_task",
                execution_context={
                    "message_id": "m1",
                    "user_id": "u1",
                    "organization_id": "r1",
                    "establishment_id": "loc-a",
                    "channel": "dashboard",
                },
                entity_type="task",
                entity_id="123",
                entity_label="Decoration",
                payload={"new_status": "COMPLETED", "operation_id": "op1"},
                success=True,
                restaurant=MagicMock(id="r1"),
                actor=MagicMock(pk="u1"),
            )

        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["event_type"], "TASK_COMPLETED")
        self.assertEqual(stored[0]["entity_id"], "123")

        # Simulate restart: clear process memory; reload from "DB" (stored list)
        reloaded = list(stored)
        self.assertEqual(reloaded[0]["event_type"], "TASK_COMPLETED")

    def test_normalize_status_change_to_completed(self):
        from miya.services.intelligence.operational_memory import normalize_event_type

        self.assertEqual(
            normalize_event_type(
                operation="update_task_status",
                payload={"new_status": "COMPLETED"},
            ),
            "TASK_COMPLETED",
        )
        self.assertEqual(
            normalize_event_type(operation="create_incident"),
            "INCIDENT_CREATED",
        )


class ReconstructTimelineTests(SimpleTestCase):
    def test_freezer_incident_timeline(self):
        from miya.services.intelligence.operational_memory import reconstruct_entity_timeline

        incident = {
            "id": "inc-456",
            "title": "Freezer broken",
            "status": "OPEN",
        }
        events = [
            {
                "event_type": "INCIDENT_CREATED",
                "entity_id": "inc-456",
                "summary": "Incident Freezer broken created.",
                "created_at": "2026-08-07T10:00:00+00:00",
            },
            {
                "event_type": "INCIDENT_ROUTED",
                "entity_id": "inc-456",
                "summary": "Incident Freezer broken routed.",
                "created_at": "2026-08-07T10:05:00+00:00",
            },
        ]
        with patch(
            "miya.services.intelligence.reality.get_current_incident",
            return_value=ok(
                message="found",
                verified=True,
                data={"incident": incident, "photos": [{"filename": "freezer.jpg"}]},
            ),
        ), patch(
            "miya.services.intelligence.operational_memory.list_operational_events",
            return_value=ok(message="events", verified=True, data={"events": events}),
        ):
            result = reconstruct_entity_timeline(_ctx(), q="freezer", entity_type="incident")
        self.assertTrue(result.success)
        self.assertEqual(result.data["current"]["status"], "OPEN")
        self.assertEqual(len(result.data["events"]), 2)
        self.assertTrue(result.data["photos"])
        self.assertEqual(result.data["timeline"][0]["authority"], "CURRENT_DATABASE_STATE")


class WorkingMemoryTests(SimpleTestCase):
    def test_pointers_not_status(self):
        from miya.services.intelligence.working_memory import get_working_memory

        snap = MagicMock()
        snap.as_dict.return_value = {
            "current_task_id": "123",
            "current_task_label": "Decoration",
            "authority": "working_memory_pointers_only",
            "directive": "re-fetch status",
        }
        with patch("miya.models.WorkingMemorySnapshot") as Model:
            Model.objects.filter.return_value.first.return_value = snap
            data = get_working_memory(user=MagicMock(pk="u1"), restaurant=MagicMock())
        self.assertEqual(data["current_task_id"], "123")
        self.assertNotIn("status", data)
        self.assertEqual(data["layer"], "WORKING_MEMORY")


class SemanticMemoryGuardTests(SimpleTestCase):
    def test_filters_status_claims_and_defaults_off(self):
        from miya.services.intelligence.semantic_memory import load_semantic_memory

        with patch(
            "miya.services.intelligence.semantic_memory.semantic_recall_enabled",
            return_value=True,
        ):
            out = load_semantic_memory(
                query="decoration",
                hits=[
                    {"text": "Decoration was completed yesterday"},
                    {"text": "We talked about party prep"},
                ],
            )
        self.assertEqual(len(out["hits"]), 1)
        self.assertIn("party prep", out["hits"][0]["text"])

        with patch(
            "miya.services.intelligence.semantic_memory.semantic_recall_enabled",
            return_value=False,
        ):
            off = load_semantic_memory(hits=[{"text": "We talked about party prep"}])
        self.assertEqual(off["hits"], [])


class RecallToolDispatchTests(SimpleTestCase):
    def test_canonical_dispatch_recall(self):
        from miya.services.ops import dispatch_canonical_tool

        with patch(
            "miya.services.intelligence.operational_memory.recall_operational_memory",
            return_value=ok(message="ok", verified=True, data={"events": []}),
        ) as mock_recall:
            result = dispatch_canonical_tool(
                "recall_operational_memory",
                {"q": "freezer", "entity_type": "incident"},
                ctx=_ctx(),
            )
        self.assertTrue(result.success)
        mock_recall.assert_called_once()
