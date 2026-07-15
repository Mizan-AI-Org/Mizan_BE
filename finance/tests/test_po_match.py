from decimal import Decimal
from unittest.mock import MagicMock

from django.test import SimpleTestCase

from finance.po_match import _amount_close, _name_score, _norm_name


class PoMatchHelpersTests(SimpleTestCase):
    def test_norm_name(self):
        self.assertEqual(_norm_name("  Metro Cash & Carry "), "metro cash carry")

    def test_name_score_contains(self):
        self.assertGreaterEqual(_name_score("Metro", "Metro Cash Carry"), 0.99)

    def test_amount_close(self):
        self.assertTrue(_amount_close(Decimal("100"), Decimal("102"), Decimal("0.05")))
        self.assertFalse(_amount_close(Decimal("100"), Decimal("120"), Decimal("0.05")))


class PoMatchSuggestSmokeTests(SimpleTestCase):
    def test_already_linked_short_circuit(self):
        from finance.po_match import suggest_po_matches

        po = MagicMock()
        po.id = "11111111-1111-1111-1111-111111111111"
        po.supplier_id = "s1"
        po.supplier.name = "Acme"
        po.total_amount = Decimal("50")
        po.status = "ORDERED"
        po.order_date = None

        inv = MagicMock()
        inv.purchase_order_id = po.id
        inv.purchase_order = po
        inv.vendor_name = "Acme"
        inv.amount = Decimal("50")
        inv.restaurant_id = "r1"

        out = suggest_po_matches(inv)
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0]["already_linked"])
