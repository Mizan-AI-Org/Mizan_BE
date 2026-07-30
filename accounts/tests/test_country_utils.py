from django.test import SimpleTestCase

from accounts.country_utils import normalize_country_code, restaurant_looks_moroccan


class CountryCodeNormalizationTests(SimpleTestCase):
    def test_my_to_ma_for_moroccan_timezone(self):
        self.assertEqual(
            normalize_country_code(
                "MY",
                timezone="Africa/Casablanca",
            ),
            "MA",
        )

    def test_my_kept_for_non_moroccan_tenant(self):
        self.assertEqual(
            normalize_country_code(
                "MY",
                timezone="Asia/Kuala_Lumpur",
                currency="MYR",
                phone="+60123456789",
                email="owner@example.com",
            ),
            "MY",
        )

    def test_language_code_ma_in_country_field(self):
        self.assertEqual(normalize_country_code("ma"), "MA")

    def test_moroccan_phone_heuristic(self):
        self.assertTrue(
            restaurant_looks_moroccan(
                phone="+212 661 234 567",
                email="owner@example.com",
            )
        )

    def test_dot_ma_email_heuristic(self):
        self.assertTrue(
            restaurant_looks_moroccan(
                email="mehdi.amranijoutey@catanzaro.ma",
            )
        )
