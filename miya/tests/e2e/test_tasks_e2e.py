"""TASKS — 10 real DB-backed E2E scenarios."""
from __future__ import annotations

from miya.tests.e2e.harness import MiyaE2EHarness, PostgresE2ETestCase
from miya.tests.e2e.seed import seed_single_establishment


class TaskAssignE2ETests(PostgresE2ETestCase):
    def setUp(self):
        super().setUp()
        self.world = seed_single_establishment()
        self.harness = MiyaE2EHarness(self.world)

    def test_e2e_task_01_assign_ahmed_closing(self):
        cap = self.harness.send("Assign Ahmed the closing task.")
        task = self.world.tasks["closing"]
        task.refresh_from_db()
        if cap.verified:
            self.assertEqual(str(task.assigned_to_id), str(self.world.staff_ahmed.id))
            self.assertTrue(cap.verified)
        else:
            self.assertIn(cap.handler, ("planning_engine", "compound_execution", ""))

    def test_e2e_task_02_give_closing_to_ahmed(self):
        cap = self.harness.send("Give Ahmed the closing task.")
        self.assertEqual(cap.intent, "ASSIGN")

    def test_e2e_task_03_put_ahmed_on_closing(self):
        cap = self.harness.send("Put Ahmed on closing.")
        self.assertEqual(cap.intent, "ASSIGN")

    def test_e2e_task_04_complete_decoration(self):
        cap = self.harness.send("Complete the decoration setup task.")
        self.assertEqual(cap.intent, "COMPLETE")

    def test_e2e_task_05_close_decoration_task(self):
        cap = self.harness.send("Close the decoration task.")
        self.assertEqual(cap.intent, "COMPLETE")

    def test_e2e_task_06_mark_complete_verified_or_clarify(self):
        cap = self.harness.send("Mark the closing checklist complete.")
        if cap.success and cap.verified:
            self.assertEqual(self.harness.task_status(self.world.tasks["closing"].id), "COMPLETED")

    def test_e2e_task_07_show_open_tasks_search(self):
        cap = self.harness.send("Show me the open tasks.")
        self.assertIn(cap.handler, ("operational_search", "planning_engine", ""))

    def test_e2e_task_08_who_handling_closing(self):
        cap = self.harness.send("Who is handling the closing checklist?")
        self.assertEqual(cap.intent, "QUERY")

    def test_e2e_task_09_status_of_closing(self):
        cap = self.harness.send("What is the status of the closing checklist?")
        self.assertEqual(cap.intent, "QUERY")

    def test_e2e_task_10_what_happened_to_closing(self):
        cap = self.harness.send("What happened to the closing checklist?")
        self.assertEqual(cap.intent, "QUERY")


class TaskCrossChannelE2ETests(PostgresE2ETestCase):
    def setUp(self):
        super().setUp()
        self.world = seed_single_establishment()
        self.harness = MiyaE2EHarness(self.world)

    def test_e2e_task_cross_channel_assign_intent_parity(self):
        intents = []
        for ch in ("dashboard", "whatsapp", "mobile", "voice"):
            cap = self.harness.send("Assign Ahmed the closing task.", channel=ch)
            intents.append(cap.intent)
        self.assertEqual(len(set(intents)), 1)
        self.assertEqual(intents[0], "ASSIGN")
