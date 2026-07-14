from decimal import Decimal

from django.test import SimpleTestCase

from menu.food_cost import _q


class FoodCostMathTests(SimpleTestCase):
    def test_quantize(self):
        self.assertEqual(_q(Decimal("1.234")), Decimal("1.23"))
        self.assertEqual(_q(None), Decimal("0"))
