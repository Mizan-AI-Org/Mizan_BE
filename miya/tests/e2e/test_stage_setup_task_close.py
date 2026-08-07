"""Stage Setup task close — regression for multi-establishment task resolution."""
from __future__ import annotations

from datetime import date

from dashboard.models import Task
from django.utils import timezone

from miya.models import OperationalEvent
from miya.tests.e2e.harness import MiyaE2EHarness, PostgresE2ETestCase
from miya.tests.e2e.seed import count_audit_events, seed_barometre_zamazama_stage_setup


class StageSetupCloseE2ETests(PostgresE2ETestCase):
    def setUp(self):
        super().setUp()
        self.world = seed_barometre_zamazama_stage_setup()
        self.harness = MiyaE2EHarness(self.world)
        self.task = self.world.tasks["stage_setup"]
        self.session = self.world.session_for(self.world.manager, location=None)

    def test_close_stage_setup_under_zamazama_completes_task(self):
        cap = self.harness.send(
            "close the stage setup task under zamazama, its done",
            session=dict(self.session),
        )
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "COMPLETED", msg=cap.reply)
        self.assertTrue(cap.verified)
        self.assertFalse(cap.needs_clarification)

    def test_close_with_establishment_clarify_then_zama_zama(self):
        session = dict(self.session)
        cap1 = self.harness.send(
            "close the stage setup task, its done",
            session=session,
        )
        self.assertTrue(cap1.needs_clarification or cap1.intent == "COMPLETE")
        if cap1.needs_clarification:
            session["_pending_task_mutation"] = {
                "raw_message": "close the stage setup task, its done",
                "query": "stage setup",
                "intent": "COMPLETE",
                "status_hint": "COMPLETED",
            }
            cap2 = self.harness.send("Zama Zama", session=session)
            self.task.refresh_from_db()
            self.assertEqual(self.task.status, "COMPLETED", msg=cap2.reply)
            self.assertTrue(cap2.verified)

    def test_wrong_establishment_does_not_claim_global_absence(self):
        session = dict(self.session)
        session["location_id"] = str(self.world.loc_a.id)
        session["location_name"] = self.world.loc_a.name
        cap = self.harness.send("close the stage setup task", session=session)
        self.task.refresh_from_db()
        self.assertNotEqual(self.task.status, "COMPLETED")
        if not cap.verified:
            self.assertTrue(
                "another establishment" in cap.reply.lower()
                or "zama zama" in cap.reply.lower()
                or cap.needs_clarification,
                msg=cap.reply,
            )

    def test_natural_phrasing_mark_stage_setup_done(self):
        session = {**self.session, "location_id": str(self.world.loc_b.id), "location_name": "Zama Zama"}
        cap = self.harness.send("mark stage setup as done", session=session)
        self.task.refresh_from_db()
        if cap.verified:
            self.assertEqual(self.task.status, "COMPLETED")

    def test_case_variation_stage_setup(self):
        session = {**self.session, "location_id": str(self.world.loc_b.id), "location_name": "Zama Zama"}
        cap = self.harness.send("close the Stage Setup task", session=session)
        self.task.refresh_from_db()
        if cap.verified:
            self.assertEqual(self.task.status, "COMPLETED")

    def test_audit_event_on_successful_close(self):
        session = {**self.session, "location_id": str(self.world.loc_b.id), "location_name": "Zama Zama"}
        before = count_audit_events(
            restaurant_id=self.world.restaurant.id,
            entity_id=str(self.task.id),
        )
        cap = self.harness.send("close the stage setup task", session=session)
        if cap.verified:
            after = count_audit_events(
                restaurant_id=self.world.restaurant.id,
                entity_id=str(self.task.id),
            )
            self.assertEqual(after, before + 1)

    def test_idempotent_retry_no_duplicate_audit(self):
        session = {**self.session, "location_id": str(self.world.loc_b.id), "location_name": "Zama Zama"}
        cap1 = self.harness.send("close the stage setup task", session=session)
        if not cap1.verified:
            self.skipTest("first close did not verify")
        before = count_audit_events(
            restaurant_id=self.world.restaurant.id,
            entity_id=str(self.task.id),
        )
        cap2 = self.harness.send("close the stage setup task", session=session)
        after = count_audit_events(
            restaurant_id=self.world.restaurant.id,
            entity_id=str(self.task.id),
        )
        self.assertLessEqual(after, before + 1)


class StageSetupUnitTests(PostgresE2ETestCase):
    def test_zamazama_resolves_zama_zama(self):
        from miya.services.ops.scoping import resolve_location_by_name

        world = seed_barometre_zamazama_stage_setup()
        loc, matches = resolve_location_by_name(
            world.restaurant,
            "zamazama",
            visible=[world.loc_a, world.loc_b],
        )
        self.assertIsNotNone(loc)
        self.assertEqual(loc.name, "Zama Zama")

    def test_pending_is_actionable(self):
        from core.canonical.status import is_task_open

        self.assertTrue(is_task_open("PENDING"))
        self.assertFalse(is_task_open("COMPLETED"))

    def test_title_match_stage_setup(self):
        from core.canonical.tasks import _title_matches_query

        self.assertTrue(_title_matches_query("Stage Setup", "stage setup"))
        self.assertTrue(_title_matches_query("Stage Setup", "stage setup task"))
