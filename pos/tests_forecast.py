"""Pure-math tests for the Phase 1/2 forecasting helpers."""

from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from inventory.unit_conversion import convert, normalize_unit, to_inventory_unit
from pos.forecast import (
    coefficient_of_variation,
    covers_multiplier,
    dynamic_buffer,
    ewma_mean,
    forecast_item,
    order_by_date,
    samples_by_item,
    suggest_order_qty,
)


class UnitConversionTests(SimpleTestCase):
    def test_normalize_aliases(self):
        self.assertEqual(normalize_unit("g"), "GRAM")
        self.assertEqual(normalize_unit("Kilos"), "KG")
        self.assertEqual(normalize_unit("ml"), "ML")
        self.assertEqual(normalize_unit("Litres"), "LITER")
        self.assertEqual(normalize_unit("pcs"), "UNIT")
        self.assertEqual(normalize_unit(None), "")

    def test_mass_conversion(self):
        qty, ok = convert(500, "g", "kg")
        self.assertTrue(ok)
        self.assertEqual(qty, Decimal("0.5"))
        qty, ok = convert(Decimal("2"), "kg", "g")
        self.assertTrue(ok)
        self.assertEqual(qty, Decimal("2000"))

    def test_volume_conversion(self):
        qty, ok = convert("1.25", "L", "ml")
        self.assertTrue(ok)
        self.assertEqual(qty, Decimal("1250"))

    def test_box_requires_pack(self):
        _, ok = convert(1, "box", "unit")
        self.assertFalse(ok)
        qty, ok = convert(1, "box", "unit", pack_size=12)
        self.assertTrue(ok)
        self.assertEqual(qty, Decimal("12"))

    def test_mismatched_families(self):
        qty, ok = convert(100, "g", "ml")
        self.assertFalse(ok)
        self.assertEqual(qty, Decimal("100"))

    def test_to_inventory_unit_same_unit_passthrough(self):
        qty, ok = to_inventory_unit(3, "kg", "kg")
        self.assertTrue(ok)
        self.assertEqual(qty, Decimal("3"))


class ForecastMathTests(SimpleTestCase):
    def test_ewma_recent_weeks_count_more(self):
        # Flat sales but with a recent spike.
        samples = [100, 50, 50, 50]
        ewma = ewma_mean(samples)
        # Weighted mean should sit between 50 and 100, closer to 100 than plain mean.
        plain = sum(samples) / len(samples)
        self.assertGreater(ewma, plain)
        self.assertLess(ewma, 100)

    def test_ewma_skips_none(self):
        self.assertEqual(ewma_mean([None, None, None, None]), 0.0)
        weighted = ewma_mean([None, 50, 50, 50])
        self.assertGreater(weighted, 40)
        self.assertLess(weighted, 60)

    def test_dynamic_buffer_clamps(self):
        # Very flat → below floor → clamp to 5%.
        self.assertAlmostEqual(dynamic_buffer([100, 100, 100, 100]), 0.05, places=3)
        # Very noisy → capped at 30%.
        self.assertEqual(dynamic_buffer([10, 100, 10, 100]), 0.30)

    def test_dynamic_buffer_fallback(self):
        self.assertEqual(dynamic_buffer([None, None, None, 100]), 0.10)

    def test_coefficient_of_variation(self):
        self.assertIsNone(coefficient_of_variation([5]))
        self.assertEqual(coefficient_of_variation([0, 0, 0, 0]), None)
        cv = coefficient_of_variation([10, 10, 10, 10])
        self.assertEqual(cv, 0.0)

    def test_forecast_item_applies_all_components(self):
        f = forecast_item([100, 50, 50, 50], covers_multiplier=1.5)
        # EWMA mean > 50 because recent weight spikes it
        self.assertGreater(f.baseline_mean, 50)
        # Final forecast = baseline × (1 + buffer) × 1.5
        expected = f.baseline_mean * (1 + f.buffer) * 1.5
        self.assertAlmostEqual(f.forecast_portions, round(expected, 3), places=2)

    def test_covers_multiplier_clamped(self):
        self.assertEqual(covers_multiplier(0, 50), 1.0)
        self.assertEqual(covers_multiplier(50, 0), 1.0)
        # 10× spike clamped to 2.0
        self.assertEqual(covers_multiplier(1000, 100), 2.0)
        # tiny vs baseline clamped to 0.5
        self.assertEqual(covers_multiplier(1, 100), 0.5)
        self.assertAlmostEqual(covers_multiplier(120, 100), 1.2, places=3)

    def test_suggest_order_qty_rounds_up_to_pack(self):
        # Need 7, pack = 12 → order 1 pack (12).
        self.assertEqual(suggest_order_qty(7, 12, None), Decimal("12.000"))
        # Need 15, pack = 12 → order 2 packs (24).
        self.assertEqual(suggest_order_qty(15, 12, None), Decimal("24.000"))
        # Need 7, no pack → order 7.
        self.assertEqual(suggest_order_qty(7, None, None), Decimal("7.000"))
        # Need 7, pack=12, min=36 → min wins.
        self.assertEqual(suggest_order_qty(7, 12, 36), Decimal("36.000"))
        # Need 0 → nothing to order.
        self.assertEqual(suggest_order_qty(0, 12, None), Decimal("0"))

    def test_order_by_date_respects_lead_time(self):
        target = date(2026, 5, 10)
        self.assertEqual(order_by_date(target, 3), date(2026, 5, 7))
        self.assertEqual(order_by_date(target, None), target)
        self.assertEqual(order_by_date(target, 0), target)

    def test_samples_by_item_preserves_missing_weeks(self):
        target = date(2026, 5, 10)
        sales = {
            date(2026, 5, 3): {"tagine": 20},
            date(2026, 4, 26): {"tagine": 18},
            # 2026-04-19 intentionally missing
            date(2026, 4, 12): {"tagine": 22},
        }
        out = samples_by_item(sales, target, weeks=4)
        self.assertIn("tagine", out)
        self.assertEqual(out["tagine"], [20.0, 18.0, None, 22.0])
