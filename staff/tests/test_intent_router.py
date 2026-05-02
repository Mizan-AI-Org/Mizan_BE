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


class FinanceBucketTests(SimpleTestCase):
    """The Finance dashboard widget needs vendor/AP/tax items, separate from PAYROLL."""

    def test_vendor_invoice_routes_to_finance(self):
        d = classify_request(
            description="Invoice 3445 from the butcher needs to be paid by Friday.",
        )
        self.assertEqual(d.destination, DEST_INBOX)
        self.assertEqual(d.category, "FINANCE")

    def test_city_tax_routes_to_finance(self):
        d = classify_request(
            subject="Last day for city tax",
            description="Today is the last day to pay the city tax.",
        )
        self.assertEqual(d.destination, DEST_INBOX)
        self.assertEqual(d.category, "FINANCE")

    def test_supplier_payment_routes_to_finance(self):
        d = classify_request(
            description="We owe the supplier payment for the beverage delivery.",
        )
        self.assertEqual(d.destination, DEST_INBOX)
        self.assertEqual(d.category, "FINANCE")

    def test_payslip_still_goes_to_payroll_not_finance(self):
        """Employee pay topics belong in PAYROLL, not FINANCE."""
        d = classify_request(description="I haven't been paid this month.")
        self.assertEqual(d.category, "PAYROLL")


class HrAndDocumentBucketTests(SimpleTestCase):
    """Onboarding/contract/dismissal copy from the dashboard mockup."""

    def test_onboarding_trainee_routes_to_hr(self):
        d = classify_request(description="Onboarding the new bar trainee tomorrow.")
        self.assertEqual(d.category, "HR")

    def test_dismissal_letter_routes_to_hr(self):
        d = classify_request(description="Need to prepare the barman dismissal letter.")
        self.assertEqual(d.category, "HR")

    def test_contracts_to_sign_routes_to_document(self):
        d = classify_request(description="Print and sign contracts for the new hires.")
        self.assertEqual(d.category, "DOCUMENT")


class MaintenanceBucketTests(SimpleTestCase):
    """Routine / preventive items go to inbox MAINTENANCE, not an incident."""

    def test_extinguishers_recharge_routes_to_maintenance_inbox(self):
        d = classify_request(description="Schedule extinguishers recharge for next month.")
        self.assertEqual(d.destination, DEST_INBOX)
        self.assertEqual(d.category, "MAINTENANCE")

    def test_oven_deepclean_routes_to_maintenance_inbox(self):
        d = classify_request(description="Oven deepcleaning quarterly task is due.")
        self.assertEqual(d.destination, DEST_INBOX)
        self.assertEqual(d.category, "MAINTENANCE")

    def test_annual_sink_maintenance_routes_to_maintenance_inbox(self):
        d = classify_request(description="Annual sink maintenance visit next week.")
        self.assertEqual(d.destination, DEST_INBOX)
        self.assertEqual(d.category, "MAINTENANCE")


class PurchaseOrderBucketTests(SimpleTestCase):
    """Procurement asks must land in PURCHASE_ORDER, not INVENTORY.

    Reported issue: "Purchasing-related requests are being incorrectly
    classified as Inventory instead of Purchases or Orders."
    """

    def test_we_need_to_buy_routes_to_purchase_order(self):
        d = classify_request(description="We need to buy 6 bottles of vodka.")
        self.assertEqual(d.destination, DEST_INBOX)
        self.assertEqual(d.category, "PURCHASE_ORDER")
        self.assertEqual(d.confidence, "high")

    def test_buy_with_arbitrary_quantity_routes_to_purchase_order(self):
        # Previously only buy 1, 2, 3, 4, 5, 6, 10, 12, 20, 24, 50, 100
        # were detected. The regex now catches any positive integer.
        d = classify_request(description="Buy 30 napkins from the supplier.")
        self.assertEqual(d.category, "PURCHASE_ORDER")

    def test_order_more_routes_to_purchase_order(self):
        d = classify_request(description="Order more flour for the bakery.")
        self.assertEqual(d.category, "PURCHASE_ORDER")

    def test_order_some_routes_to_purchase_order(self):
        d = classify_request(description="Please order some olive oil.")
        self.assertEqual(d.category, "PURCHASE_ORDER")

    def test_reorder_routes_to_purchase_order(self):
        # "reorder" used to live in INVENTORY, which was wrong — a
        # reorder is a buying action, not a stock observation.
        d = classify_request(description="We should reorder the napkins this week.")
        self.assertEqual(d.category, "PURCHASE_ORDER")

    def test_re_order_with_dash_routes_to_purchase_order(self):
        d = classify_request(description="Need to re-order the tonic water.")
        self.assertEqual(d.category, "PURCHASE_ORDER")

    def test_restock_routes_to_purchase_order(self):
        d = classify_request(description="Please restock the bar with vodka.")
        self.assertEqual(d.category, "PURCHASE_ORDER")

    def test_re_stock_with_dash_routes_to_purchase_order(self):
        d = classify_request(description="Re-stock the dry goods this afternoon.")
        self.assertEqual(d.category, "PURCHASE_ORDER")

    def test_stock_up_routes_to_purchase_order(self):
        d = classify_request(description="Let's stock up on flour before the weekend.")
        self.assertEqual(d.category, "PURCHASE_ORDER")

    def test_running_low_with_buy_verb_routes_to_purchase_order(self):
        """Mixed observation + action: the action (buy) wins."""
        d = classify_request(
            description="We're running low on vodka — please order 6 bottles.",
        )
        self.assertEqual(d.category, "PURCHASE_ORDER")

    def test_place_a_po_routes_to_purchase_order(self):
        d = classify_request(description="Place a PO with the butcher today.")
        self.assertEqual(d.category, "PURCHASE_ORDER")

    def test_place_an_order_routes_to_purchase_order(self):
        d = classify_request(description="Please place an order with our supplier.")
        self.assertEqual(d.category, "PURCHASE_ORDER")

    def test_purchase_request_routes_to_purchase_order(self):
        d = classify_request(description="Submit a purchase request for new gloves.")
        self.assertEqual(d.category, "PURCHASE_ORDER")

    def test_french_acheter_routes_to_purchase_order(self):
        d = classify_request(
            description="Il faut acheter 50 kilos de farine ce matin.",
        )
        self.assertEqual(d.category, "PURCHASE_ORDER")

    def test_french_passer_une_commande_routes_to_purchase_order(self):
        d = classify_request(
            description="Peux-tu passer une commande chez le fournisseur ?",
        )
        self.assertEqual(d.category, "PURCHASE_ORDER")

    def test_french_bon_de_commande_routes_to_purchase_order(self):
        d = classify_request(description="Préparer le bon de commande pour les boissons.")
        self.assertEqual(d.category, "PURCHASE_ORDER")

    def test_pure_low_stock_observation_stays_in_inventory(self):
        """Regression: an observation without buying intent stays in INVENTORY."""
        d = classify_request(description="We are running out of olive oil and napkins.")
        self.assertEqual(d.category, "INVENTORY")

    def test_pure_inventory_count_stays_in_inventory(self):
        d = classify_request(description="The stock count for last week looks off.")
        self.assertEqual(d.category, "INVENTORY")

    def test_purchase_intent_overrides_agent_label_inventory(self):
        """If Miya mislabels a buy request as INVENTORY, we fix it."""
        d = classify_request(
            description="We need to buy 6 bottles of vodka.",
            agent_category="INVENTORY",
        )
        self.assertEqual(d.category, "PURCHASE_ORDER")

    def test_purchase_intent_does_not_override_explicit_hr(self):
        """Procurement verb in an HR-labelled row keeps HR (e.g. 'order
        Karim's HR file') — we only override OTHER / INVENTORY / blank."""
        d = classify_request(
            description="Please order Karim's HR file from the cabinet.",
            agent_category="HR",
        )
        self.assertEqual(d.category, "HR")

    def test_purchase_intent_does_not_override_explicit_finance(self):
        """A buying verb shouldn't steal from FINANCE either — vendor
        invoices are paid, not purchased."""
        d = classify_request(
            description="Please buy back the credit on invoice 3445.",
            agent_category="FINANCE",
        )
        self.assertEqual(d.category, "FINANCE")


class MeetingBucketTests(SimpleTestCase):
    """The MEETING bucket powers the Meetings & Reminders widget for tasks."""

    def test_meeting_with_person_routes_to_meeting(self):
        d = classify_request(description="Meeting with M. Rockefeller on Thursday.")
        self.assertEqual(d.category, "MEETING")

    def test_remind_me_to_routes_to_meeting(self):
        d = classify_request(description="Remind me to call Zoe Karl tomorrow morning.")
        self.assertEqual(d.category, "MEETING")
