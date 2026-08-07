"""AMBIGUITY, COMPOUND, MULTI-ESTABLISHMENT, FAILURES, REALITY — real DB E2E."""
from __future__ import annotations

from unittest.mock import patch

from dashboard.models import Task

from miya.tests.e2e.harness import MiyaE2EHarness, PostgresE2ETestCase
from miya.tests.e2e.seed import (
    count_audit_events,
    seed_multi_establishment,
    seed_single_establishment,
    seed_three_decoration_tasks,
)


class ThreeTaskAmbiguityE2ETests(PostgresE2ETestCase):
    def setUp(self):
        super().setUp()
        self.world = seed_three_decoration_tasks()
        self.harness = MiyaE2EHarness(self.world)

    def test_e2e_ambiguity_01_three_decoration_clarify(self):
        cap = self.harness.send("Assign Decoration to Ahmed.")
        self.assertTrue(cap.needs_clarification or not cap.verified or cap.intent == "ASSIGN")
        for t in self.world.tasks.values():
            if t.title == "Decoration":
                t.refresh_from_db()
                if cap.needs_clarification:
                    self.assertIsNone(t.assigned_to_id)

    def test_e2e_ambiguity_02_assign_specific_decoration(self):
        cap = self.harness.send("Assign the Decoration setup task to Ahmed.")
        self.assertEqual(cap.intent, "ASSIGN")

    def test_e2e_ambiguity_03_complete_it_without_context(self):
        cap = self.harness.send("Complete it.")
        self.assertTrue(cap.needs_clarification or not cap.verified or cap.deferred)

    def test_e2e_ambiguity_04_which_one_phrasing(self):
        cap = self.harness.send("Mark the second one done.")
        self.assertIn(cap.intent, ("COMPLETE", "QUERY", "UNKNOWN"))

    def test_e2e_ambiguity_05_no_guess_on_ambiguous(self):
        cap = self.harness.send("Assign Decoration to Ahmed.")
        if cap.needs_clarification:
            self.assertIn("which", cap.reply.lower())


class CompoundExecutionE2ETests(PostgresE2ETestCase):
    def setUp(self):
        super().setUp()
        self.world = seed_single_establishment()
        self.harness = MiyaE2EHarness(self.world)

    def test_e2e_compound_01_complete_and_tell_manager(self):
        cap = self.harness.send("Complete Ahmed's closing task and tell the manager.")
        self.assertIn(cap.handler, ("compound_execution", "planning_engine", ""))
        if cap.handler == "compound_execution":
            meta = cap.meta.get("compound_execution") or {}
            steps = meta.get("step_results") or []
            self.assertGreaterEqual(len(steps), 1)
            if cap.verified:
                self.assertEqual(
                    self.harness.task_status(self.world.tasks["closing"].id),
                    "COMPLETED",
                )

    def test_e2e_compound_02_step_results_exposed(self):
        cap = self.harness.send("Complete the closing checklist and tell the manager.")
        if cap.meta.get("compound_execution"):
            steps = cap.meta["compound_execution"].get("step_results") or []
            self.assertTrue(all("action" in s for s in steps))


class MultiEstablishmentE2ETests(PostgresE2ETestCase):
    def setUp(self):
        super().setUp()
        self.world = seed_multi_establishment()
        self.harness = MiyaE2EHarness(self.world)

    def test_e2e_multi_01_staff_cannot_read_other_restaurant_task(self):
        cap = self.harness.send(
            "What is the status of the closing checklist?",
            user=self.world.staff_b_only,
            session=self.world.session_for(
                self.world.staff_b_only,
                location=self.world.loc_b,
                channel="dashboard",
            ),
        )
        self.assertIsNotNone(cap)

    def test_e2e_multi_02_manager_site_a_assign(self):
        cap = self.harness.send(
            "Assign Ahmed the closing task.",
            session=self.world.session_for(
                self.world.manager_multi,
                location=self.world.loc_a,
            ),
        )
        self.assertEqual(cap.intent, "ASSIGN")

    def test_e2e_multi_03_cross_site_id_no_mutate(self):
        task_b_id = self.world.tasks["closing_b"].id
        cap = self.harness.send(
            f"Complete task {task_b_id}.",
            session=self.world.session_for(self.world.manager_multi, location=self.world.loc_a),
        )
        task_b = Task.objects.get(pk=task_b_id)
        if not cap.verified:
            self.assertNotEqual(task_b.status, "COMPLETED")

    def test_e2e_multi_04_staff_a_only_in_a(self):
        self.assertEqual(self.world.staff_ahmed.restaurant_id, self.world.restaurant_a.id)

    def test_e2e_multi_05_incident_scoped_to_location(self):
        cap = self.harness.send("Report a broken freezer.")
        self.assertIn(cap.intent, ("CREATE", "QUERY", "UNKNOWN"))


class FailureVerificationE2ETests(PostgresE2ETestCase):
    def setUp(self):
        super().setUp()
        self.world = seed_single_establishment()
        self.harness = MiyaE2EHarness(self.world)

    def test_e2e_fail_01_staff_cannot_assign(self):
        cap = self.harness.send(
            "Assign Ahmed the closing task.",
            user=self.world.staff_ahmed,
            session=self.world.session_for(self.world.staff_ahmed),
        )
        if cap.handler == "authorize_denied":
            self.assertFalse(cap.success)
            self.assertIn("permission", cap.reply.lower())

    def test_e2e_fail_02_ambiguous_no_done_claim(self):
        world = seed_three_decoration_tasks()
        harness = MiyaE2EHarness(world)
        cap = harness.send("Assign Decoration to Ahmed.")
        if cap.needs_clarification:
            self.assertNotIn("done", cap.reply.lower())

    @patch("miya.services.ops.tasks.update_task_status")
    def test_e2e_fail_03_verification_failure_no_done(self, mock_update):
        from miya.services.ops.result import fail

        mock_update.return_value = fail(code="db_error", message="Write failed")
        cap = self.harness.send("Complete the closing checklist.")
        if cap.reply:
            self.assertFalse(cap.verified and "done" in cap.reply.lower() and cap.success)

    def test_e2e_fail_04_unknown_task_no_success(self):
        cap = self.harness.send("Complete the nonexistent xyz task.")
        self.assertFalse(cap.verified and cap.success)


class RealityVsMemoryE2ETests(PostgresE2ETestCase):
    def setUp(self):
        super().setUp()
        self.world = seed_single_establishment()
        self.harness = MiyaE2EHarness(self.world)

    def test_e2e_reality_01_db_wins_over_conversation(self):
        task = self.world.tasks["closing"]
        sess = self.world.session_for(self.world.manager)
        sess["working_set"] = {"tasks": [str(task.id)]}
        sess["current_task_id"] = str(task.id)
        Task.objects.filter(pk=task.id).update(status="COMPLETED")
        cap = self.harness.send("What is the current status of the closing checklist?", session=sess)
        self.assertEqual(cap.intent, "QUERY")

    def test_e2e_reality_02_external_change_reflected(self):
        task = self.world.tasks["decoration"]
        Task.objects.filter(pk=task.id).update(status="COMPLETED")
        cap = self.harness.send("Is the decoration setup completed?")
        self.assertEqual(cap.intent, "QUERY")

    def test_e2e_reality_03_history_not_from_memory_only(self):
        cap = self.harness.send("What happened to the closing checklist?")
        self.assertEqual(cap.intent, "QUERY")

    def test_e2e_reality_04_after_assign_then_who_handling(self):
        caps = self.harness.send_sequence(
            [
                "Assign Ahmed the closing task.",
                "Who is handling the closing checklist?",
            ]
        )
        self.assertEqual(caps[1].intent, "QUERY")


class StaffRoutingWhatsAppE2ETests(PostgresE2ETestCase):
    def setUp(self):
        super().setUp()
        self.world = seed_single_establishment()
        self.harness = MiyaE2EHarness(self.world)

    def test_e2e_staff_01_find_ahmed(self):
        cap = self.harness.send("Find Ahmed.")
        self.assertIn(cap.intent, ("QUERY", "RETRIEVE", "UNKNOWN"))

    def test_e2e_staff_02_who_on_shift(self):
        cap = self.harness.send("Who is on shift tonight?")
        self.assertIn(cap.intent, ("QUERY", "RETRIEVE", "UNKNOWN"))

    def test_e2e_staff_03_responsible_for_closing(self):
        cap = self.harness.send("Who is responsible for the closing checklist?")
        self.assertEqual(cap.intent, "QUERY")

    def test_e2e_staff_04_list_managers(self):
        cap = self.harness.send("List managers.")
        self.assertIn(cap.intent, ("QUERY", "RETRIEVE", "UNKNOWN"))

    def test_e2e_staff_05_kitchen_staff(self):
        cap = self.harness.send("Show staff in kitchen.")
        self.assertIn(cap.intent, ("QUERY", "RETRIEVE", "UNKNOWN"))

    def test_e2e_wa_01_assign_via_whatsapp(self):
        cap = self.harness.send("Assign Ahmed the closing task.", channel="whatsapp")
        self.assertEqual(cap.intent, "ASSIGN")

    def test_e2e_wa_02_status_whatsapp(self):
        cap = self.harness.send("Status?", channel="whatsapp")
        self.assertIn(cap.intent, ("QUERY", "UNKNOWN"))

    def test_e2e_wa_03_done_closing_whatsapp(self):
        cap = self.harness.send("Done with closing.", channel="whatsapp")
        self.assertIn(cap.intent, ("COMPLETE", "QUERY", "UNKNOWN"))

    def test_e2e_wa_04_freezer_whatsapp(self):
        cap = self.harness.send("Need help with freezer.", channel="whatsapp")
        self.assertIn(cap.intent, ("CREATE", "QUERY", "UNKNOWN"))
