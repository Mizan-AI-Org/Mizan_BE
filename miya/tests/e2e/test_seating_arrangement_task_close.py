"""Seating Arrangement task close — establishment clarify + title-as-task_id."""
from __future__ import annotations

from miya.tests.e2e.harness import MiyaE2EHarness, PostgresE2ETestCase
from miya.tests.e2e.seed import seed_barometre_seating_arrangement


class SeatingArrangementCloseE2ETests(PostgresE2ETestCase):
    def setUp(self):
        super().setUp()
        self.world = seed_barometre_seating_arrangement()
        self.harness = MiyaE2EHarness(self.world)
        self.task = self.world.tasks["seating_arrangement"]
        session = self.world.session_for(self.world.manager, location=None)
        session.pop("location_id", None)
        session.pop("location_name", None)
        self.session = session

    def test_close_with_location_set(self):
        session = {
            **self.session,
            "location_id": str(self.world.loc_a.id),
            "location_name": "Barometre - Main",
        }
        cap = self.harness.send(
            "close the seating arrangement task, its done!",
            session=session,
        )
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "COMPLETED", msg=cap.reply)
        self.assertTrue(cap.verified)

    def test_clarify_then_barometre_main_via_working_memory(self):
        session = dict(self.session)
        cap1 = self.harness.send(
            "close the seating arrangement task, its done!",
            session=session,
        )
        self.assertTrue(
            cap1.needs_clarification or "which establishment" in cap1.reply.lower(),
            msg=cap1.reply,
        )
        history = [
            {"role": "user", "content": "close the seating arrangement task, its done!"},
            {"role": "assistant", "content": cap1.reply},
        ]
        cap2 = self.harness.send(
            "Barometre - Main",
            session=dict(self.session),
            history=history,
        )
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "COMPLETED", msg=cap2.reply)
        self.assertTrue(cap2.verified)

    def test_clarify_then_barometre_main_via_history_recovery(self):
        """Even without working memory, history + establishment reply should resume."""
        session = dict(self.session)
        cap1 = self.harness.send(
            "close the seating arrangement task, its done!",
            session=session,
        )
        self.assertTrue(cap1.needs_clarification or "which establishment" in cap1.reply.lower())
        from miya.services.intelligence.pending_mutation import clear_pending_task_mutation

        clear_pending_task_mutation(user=self.world.manager, restaurant=self.world.restaurant)
        history = [
            {"role": "user", "content": "close the seating arrangement task, its done!"},
            {"role": "assistant", "content": cap1.reply},
        ]
        cap2 = self.harness.send(
            "Barometre - Main",
            session=dict(self.session),
            history=history,
        )
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "COMPLETED", msg=cap2.reply)


class SeatingArrangementUnitTests(PostgresE2ETestCase):
    def test_title_in_task_id_normalized(self):
        from core.canonical.tasks import is_record_id, resolve_canonical_task
        from miya.services.ops import _normalize_task_lookup_args

        self.assertFalse(is_record_id("seating arrangement"))
        task_id, q = _normalize_task_lookup_args("seating arrangement", "")
        self.assertEqual(task_id, "")
        self.assertEqual(q, "seating arrangement")

        world = seed_barometre_seating_arrangement()
        task, origin, meta = resolve_canonical_task(
            world.restaurant,
            task_id="seating arrangement",
            q="",
            location_id=str(world.loc_a.id),
        )
        self.assertIsNotNone(task)
        self.assertEqual(str(task.id), str(world.tasks["seating_arrangement"].id))
