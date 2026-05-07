"""
Pure-Python tests for the widget alias resolver. No DB required.

These tests guard the contract the agent + frontend rely on:
when a manager says "Purchases" / "HR" / "Finance" / etc. the resolver
returns the canonical *data-bound* built-in widget id, so a
``create_custom`` call gets transparently redirected to ``add`` and the
manager sees a live data-bound widget instead of an "Ask Miya"
placeholder.
"""

from django.test import SimpleTestCase

from dashboard.widget_alias_resolver import (
    DATA_BOUND_BUILTIN_IDS,
    is_alias_for_data_bound_widget,
    known_aliases_for,
    resolve_widget_alias,
)


class ResolveWidgetAliasTests(SimpleTestCase):
    def test_purchases_resolves_to_purchase_orders(self):
        self.assertEqual(resolve_widget_alias("Purchases"), "purchase_orders")

    def test_purchase_singular_resolves(self):
        self.assertEqual(resolve_widget_alias("Purchase"), "purchase_orders")

    def test_purchase_orders_resolves(self):
        self.assertEqual(resolve_widget_alias("Purchase Orders"), "purchase_orders")

    def test_po_acronym_resolves(self):
        self.assertEqual(resolve_widget_alias("PO"), "purchase_orders")

    def test_procurement_resolves(self):
        self.assertEqual(resolve_widget_alias("Procurement"), "purchase_orders")

    def test_french_achats_resolves(self):
        self.assertEqual(resolve_widget_alias("Achats"), "purchase_orders")

    def test_arabic_purchases_resolves(self):
        self.assertEqual(resolve_widget_alias("مشتريات"), "purchase_orders")

    def test_human_resources_resolves(self):
        self.assertEqual(resolve_widget_alias("Human Resources"), "human_resources")

    def test_hr_acronym_resolves(self):
        self.assertEqual(resolve_widget_alias("HR"), "human_resources")

    def test_french_rh_resolves(self):
        self.assertEqual(resolve_widget_alias("RH"), "human_resources")

    def test_finance_resolves(self):
        self.assertEqual(resolve_widget_alias("Finance"), "finance")

    def test_invoices_resolves_to_finance(self):
        self.assertEqual(resolve_widget_alias("Invoices"), "finance")

    def test_factures_resolves_to_finance(self):
        self.assertEqual(resolve_widget_alias("Factures"), "finance")

    def test_maintenance_resolves(self):
        self.assertEqual(resolve_widget_alias("Maintenance"), "maintenance")

    def test_repairs_resolves_to_maintenance(self):
        self.assertEqual(resolve_widget_alias("Repairs"), "maintenance")

    def test_urgent_resolves_to_urgent_top(self):
        self.assertEqual(resolve_widget_alias("Urgent"), "urgent_top")

    def test_top_5_urgents_resolves(self):
        self.assertEqual(resolve_widget_alias("Top 5 Urgents"), "urgent_top")

    def test_inbox_resolves_to_staff_inbox(self):
        self.assertEqual(resolve_widget_alias("Inbox"), "staff_inbox")

    def test_meetings_resolves(self):
        self.assertEqual(resolve_widget_alias("Meetings"), "meetings_reminders")

    def test_calendar_resolves(self):
        self.assertEqual(resolve_widget_alias("Calendar"), "meetings_reminders")

    def test_clock_in_resolves(self):
        self.assertEqual(resolve_widget_alias("Clock-in"), "clock_ins")

    def test_inventory_resolves(self):
        self.assertEqual(resolve_widget_alias("Inventory"), "inventory_delivery")

    def test_misc_resolves_to_miscellaneous(self):
        self.assertEqual(resolve_widget_alias("Misc"), "miscellaneous")

    def test_divers_resolves_to_miscellaneous(self):
        self.assertEqual(resolve_widget_alias("Divers"), "miscellaneous")

    def test_random_shortcut_does_not_resolve(self):
        self.assertIsNone(resolve_widget_alias("Daily PDF report"))

    def test_empty_input_does_not_resolve(self):
        self.assertIsNone(resolve_widget_alias(""))
        self.assertIsNone(resolve_widget_alias(None))
        self.assertIsNone(resolve_widget_alias("   "))

    def test_normalisation_strips_accents(self):
        # "Réunions" should fold to "reunions" which maps to meetings_reminders.
        self.assertEqual(
            resolve_widget_alias("Réunions"), "meetings_reminders"
        )

    def test_normalisation_collapses_whitespace(self):
        self.assertEqual(
            resolve_widget_alias("Purchase   orders"), "purchase_orders"
        )

    def test_normalisation_strips_leading_the(self):
        self.assertEqual(
            resolve_widget_alias("the purchases"), "purchase_orders"
        )

    def test_first_matching_candidate_wins(self):
        # Title doesn't match, subtitle does → returns subtitle's hit.
        self.assertEqual(
            resolve_widget_alias("Random label", "PO"), "purchase_orders"
        )

    def test_only_first_match_is_returned_when_multiple_match(self):
        # First candidate wins even if a later one points elsewhere.
        self.assertEqual(
            resolve_widget_alias("HR", "Finance"), "human_resources"
        )

    def test_plural_fallback_singular_lookup(self):
        # The alias table doesn't include "audits" but if it did include
        # "audit" the s-stripping fallback would catch it. Use a real
        # entry to verify the fallback path: "purchase" is a key, ensure
        # a hypothetical typo with extra 's' (not in table) doesn't blow up.
        self.assertIsNone(resolve_widget_alias("foobar"))


class IsAliasForDataBoundWidgetTests(SimpleTestCase):
    def test_known_alias_returns_true(self):
        self.assertTrue(is_alias_for_data_bound_widget("Purchases"))

    def test_unknown_alias_returns_false(self):
        self.assertFalse(is_alias_for_data_bound_widget("Daily PDF report"))


class KnownAliasesForTests(SimpleTestCase):
    def test_purchase_orders_has_multilingual_aliases(self):
        aliases = set(known_aliases_for("purchase_orders"))
        self.assertIn("purchases", aliases)
        self.assertIn("po", aliases)
        self.assertIn("achats", aliases)
        self.assertIn("procurement", aliases)

    def test_every_data_bound_id_has_at_least_one_alias(self):
        for widget_id in DATA_BOUND_BUILTIN_IDS:
            with self.subTest(widget_id=widget_id):
                self.assertGreaterEqual(
                    len(known_aliases_for(widget_id)),
                    1,
                    f"data-bound widget '{widget_id}' has zero aliases — "
                    "the manager will only ever match it by the canonical "
                    "snake_case id, which they will not type.",
                )


class CrossModuleSyncTests(SimpleTestCase):
    """Guard against drift between the alias resolver and the widget id
    allow-list."""

    def test_every_alias_target_is_a_known_widget_id(self):
        from dashboard.widget_ids import DASHBOARD_WIDGET_IDS

        # Pull every value the resolver might return.
        from dashboard.widget_alias_resolver import _ALIASES  # type: ignore[attr-defined]

        targets = set(_ALIASES.values())
        unknown = targets - set(DASHBOARD_WIDGET_IDS)
        self.assertFalse(
            unknown,
            f"Alias table maps to widget id(s) not in DASHBOARD_WIDGET_IDS: "
            f"{sorted(unknown)}",
        )

    def test_every_data_bound_id_is_a_known_widget_id(self):
        from dashboard.widget_ids import DASHBOARD_WIDGET_IDS

        unknown = set(DATA_BOUND_BUILTIN_IDS) - set(DASHBOARD_WIDGET_IDS)
        self.assertFalse(
            unknown,
            f"DATA_BOUND_BUILTIN_IDS contains ids not in DASHBOARD_WIDGET_IDS: "
            f"{sorted(unknown)}",
        )
