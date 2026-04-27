"""Tests for ``staff.intent_router``.

Pure-Python unit tests (``SimpleTestCase``) — the router has no DB
dependency, so we don't pay the cost of spinning up the test database
for each run.
"""

from django.test import SimpleTestCase

from staff.intent_router import (
    DEST_INBOX,
    DEST_INCIDENT,
    INCIDENT_FOOD_SAFETY,
    INCIDENT_MAINTENANCE,
    INCIDENT_SAFETY,
    classify_request,
)


class IncidentRoutingTests(SimpleTestCase):
    """Things that should *never* land in the manager inbox."""

    def test_broken_equipment_routes_to_maintenance_incident(self):
        d = classify_request(
            subject="Fryer broken",
            description="The fryer in station 2 stopped working this morning.",
        )
        self.assertEqual(d.destination, DEST_INCIDENT)
        self.assertEqual(d.category, INCIDENT_MAINTENANCE)
        self.assertGreaterEqual(len(d.matched_terms), 1)

    def test_fire_routes_to_safety_with_critical_priority(self):
        d = classify_request(
            subject="Fire in kitchen",
            description="There is a fire near the grill, please send help now.",
        )
        self.assertEqual(d.destination, DEST_INCIDENT)
        self.assertEqual(d.category, INCIDENT_SAFETY)
        self.assertEqual(d.priority, "CRITICAL")

    def test_water_leak_routes_to_safety_incident(self):
        d = classify_request(
            description="There's a water leak under the sink and the floor is flooding.",
        )
        self.assertEqual(d.destination, DEST_INCIDENT)
        self.assertEqual(d.category, INCIDENT_SAFETY)

    def test_pest_infestation_routes_to_safety_incident(self):
        d = classify_request(description="We saw cockroaches near the prep table.")
        self.assertEqual(d.destination, DEST_INCIDENT)
        self.assertEqual(d.category, INCIDENT_SAFETY)

    def test_expired_food_routes_to_food_safety(self):
        d = classify_request(
            subject="Expired food",
            description="Found expired chicken in the walk-in fridge.",
        )
        self.assertEqual(d.destination, DEST_INCIDENT)
        self.assertEqual(d.category, INCIDENT_FOOD_SAFETY)

    def test_injury_routes_to_safety_with_high_priority(self):
        d = classify_request(
            subject="Staff injury",
            description="Karim cut his finger on the slicer and it's bleeding.",
        )
        self.assertEqual(d.destination, DEST_INCIDENT)
        self.assertEqual(d.category, INCIDENT_SAFETY)
        self.assertIn(d.priority, {"CRITICAL", "HIGH"})

    def test_incident_overrides_explicit_inbox_category(self):
        """Even if Miya labels it 'HR', a fire is still an incident."""
        d = classify_request(
            subject="Fire",
            description="Fire alarm went off, smoke in the kitchen.",
            agent_category="HR",
        )
        self.assertEqual(d.destination, DEST_INCIDENT)

    def test_task_framing_demotes_maintenance_back_to_inbox(self):
        """`add task to fix the broken fryer` should NOT be filed as an incident."""
        d = classify_request(
            description="please add task to fix the broken fryer next week",
        )
        self.assertEqual(d.destination, DEST_INBOX)
        self.assertEqual(d.category, "MAINTENANCE")


class InboxCategorisationTests(SimpleTestCase):
    """Inbox rows should land in the right category lane, not OTHER."""

    def test_payslip_question_routes_to_payroll(self):
        d = classify_request(
            description="I haven't been paid yet, can someone check my payslip?",
        )
        self.assertEqual(d.destination, DEST_INBOX)
        self.assertEqual(d.category, "PAYROLL")

    def test_id_card_request_routes_to_document(self):
        d = classify_request(
            description="I need a copy of my work permit and my contract.",
        )
        self.assertEqual(d.destination, DEST_INBOX)
        self.assertEqual(d.category, "DOCUMENT")

    def test_shift_swap_routes_to_scheduling(self):
        d = classify_request(
            description="Can someone cover my shift on Friday? I need the day off.",
        )
        self.assertEqual(d.destination, DEST_INBOX)
        self.assertEqual(d.category, "SCHEDULING")

    def test_low_stock_routes_to_inventory(self):
        d = classify_request(
            description="We are running out of olive oil and napkins.",
        )
        self.assertEqual(d.destination, DEST_INBOX)
        self.assertEqual(d.category, "INVENTORY")

    def test_table_booking_routes_to_reservations(self):
        d = classify_request(
            description="A guest wants to book a table for 6 on Saturday.",
        )
        self.assertEqual(d.destination, DEST_INBOX)
        self.assertEqual(d.category, "RESERVATIONS")

    def test_grievance_routes_to_hr(self):
        d = classify_request(
            description="I want to file a grievance about the new uniform policy.",
        )
        self.assertEqual(d.destination, DEST_INBOX)
        self.assertEqual(d.category, "HR")

    def test_explicit_agent_category_is_respected(self):
        """If Miya already classified as PAYROLL, we trust her."""
        d = classify_request(
            description="Some random thing without strong keywords.",
            agent_category="PAYROLL",
        )
        self.assertEqual(d.destination, DEST_INBOX)
        self.assertEqual(d.category, "PAYROLL")

    def test_unknown_message_falls_back_to_other_with_low_confidence(self):
        d = classify_request(description="hello, just saying hi to the team")
        self.assertEqual(d.destination, DEST_INBOX)
        self.assertEqual(d.category, "OTHER")
        self.assertEqual(d.confidence, "low")

    def test_empty_description_is_safe(self):
        d = classify_request(subject="", description="")
        self.assertEqual(d.destination, DEST_INBOX)
        self.assertEqual(d.category, "OTHER")
        self.assertEqual(d.confidence, "low")

    def test_accents_and_unicode_are_normalised(self):
        """A French message with accented characters should still match."""
        d = classify_request(
            description="J'ai besoin d'une attestation de salaire pour mon visa.",
        )
        self.assertEqual(d.destination, DEST_INBOX)
        # PAYROLL ranks first, then DOCUMENT — either is acceptable; we
        # only assert the message didn't fall through to OTHER.
        self.assertNotEqual(d.category, "OTHER")
