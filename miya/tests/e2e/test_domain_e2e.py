"""INCIDENTS, DOCUMENTS, INVOICES, REMINDERS, MEETINGS — real DB E2E."""
from __future__ import annotations

from miya.tests.e2e.harness import MiyaE2EHarness, PostgresE2ETestCase
from miya.tests.e2e.seed import (
    seed_document,
    seed_incident,
    seed_invoice,
    seed_reminder,
    seed_single_establishment,
)


class IncidentE2ETests(PostgresE2ETestCase):
    def setUp(self):
        super().setUp()
        self.world = seed_single_establishment()
        seed_incident(world=self.world, title="Freezer broken")
        self.harness = MiyaE2EHarness(self.world)

    def test_e2e_incident_01_freezer_broken_create_intent(self):
        cap = self.harness.send("The freezer is broken.")
        self.assertIn(cap.intent, ("CREATE", "UNKNOWN"))

    def test_e2e_incident_02_issue_with_freezer(self):
        cap = self.harness.send("There's an issue with the freezer.")
        self.assertIn(cap.intent, ("CREATE", "QUERY", "UNKNOWN"))

    def test_e2e_incident_03_what_happened_to_freezer(self):
        cap = self.harness.send("What happened to the freezer incident?")
        self.assertEqual(cap.intent, "QUERY")

    def test_e2e_incident_04_status_freezer(self):
        cap = self.harness.send("What is the status of the freezer broken incident?")
        self.assertIn(cap.intent, ("QUERY", "CREATE"))

    def test_e2e_incident_05_forward_to_maintenance(self):
        cap = self.harness.send("Send this to maintenance.")
        self.assertIn(cap.intent, ("ROUTE", "CREATE", "QUERY"))


class DocumentE2ETests(PostgresE2ETestCase):
    def setUp(self):
        super().setUp()
        self.world = seed_single_establishment()
        seed_document(world=self.world, title="Insurance policy")
        self.harness = MiyaE2EHarness(self.world)

    def test_e2e_doc_01_show_insurance(self):
        cap = self.harness.send("Show me the insurance.")
        self.assertIn(cap.intent, ("RETRIEVE", "QUERY"))

    def test_e2e_doc_02_insurance_expiry(self):
        cap = self.harness.send("When does the insurance expire?")
        self.assertEqual(cap.intent, "QUERY")

    def test_e2e_doc_03_remind_insurance(self):
        cap = self.harness.send("Remind me about the insurance.")
        self.assertIn(cap.intent, ("REMIND", "QUERY"))

    def test_e2e_doc_04_pdf_says(self):
        cap = self.harness.send("What does this PDF say?")
        self.assertIn(cap.intent, ("RETRIEVE", "QUERY"))

    def test_e2e_doc_05_show_document_again(self):
        cap = self.harness.send("Show me the document again.")
        self.assertIn(cap.intent, ("RETRIEVE", "QUERY"))


class InvoiceE2ETests(PostgresE2ETestCase):
    def setUp(self):
        super().setUp()
        self.world = seed_single_establishment()
        seed_invoice(world=self.world, vendor="ABC Foods")
        self.harness = MiyaE2EHarness(self.world)

    def test_e2e_inv_01_approve_invoice(self):
        cap = self.harness.send("Approve this invoice.")
        self.assertIn(cap.intent, ("APPROVE", "QUERY"))

    def test_e2e_inv_02_why_not_paid(self):
        cap = self.harness.send("Why hasn't this invoice been paid?")
        self.assertEqual(cap.intent, "QUERY")

    def test_e2e_inv_03_who_approved(self):
        cap = self.harness.send("Who approved this invoice?")
        self.assertEqual(cap.intent, "QUERY")

    def test_e2e_inv_04_invoice_history(self):
        cap = self.harness.send("Show me the invoice history.")
        self.assertEqual(cap.intent, "QUERY")

    def test_e2e_inv_05_status_abc_foods(self):
        cap = self.harness.send("What is the status of the ABC Foods invoice?")
        self.assertEqual(cap.intent, "QUERY")


class ReminderMeetingE2ETests(PostgresE2ETestCase):
    def setUp(self):
        super().setUp()
        self.world = seed_single_establishment()
        seed_reminder(world=self.world)
        self.harness = MiyaE2EHarness(self.world)

    def test_e2e_reminder_01_list_reminders(self):
        cap = self.harness.send("Show my reminders.")
        self.assertIn(cap.intent, ("QUERY", "RETRIEVE"))

    def test_e2e_reminder_02_remind_payroll(self):
        cap = self.harness.send("Set a reminder for payroll.")
        self.assertIn(cap.intent, ("REMIND", "CREATE", "QUERY"))

    def test_e2e_reminder_03_insurance_reminder(self):
        cap = self.harness.send("Remind me about the insurance renewal.")
        self.assertIn(cap.intent, ("REMIND", "QUERY"))

    def test_e2e_meeting_01_kitchen(self):
        cap = self.harness.send("Set up a meeting with the kitchen.")
        self.assertEqual(cap.intent, "SCHEDULE")

    def test_e2e_meeting_02_foh(self):
        cap = self.harness.send("Arrange one for front of house.")
        self.assertIn(cap.intent, ("SCHEDULE", "QUERY"))

    def test_e2e_meeting_03_hr(self):
        cap = self.harness.send("Schedule a meeting with HR.")
        self.assertEqual(cap.intent, "SCHEDULE")
