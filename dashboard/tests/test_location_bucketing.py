"""Branch attribution helpers."""
from django.test import SimpleTestCase

from dashboard.api.location_bucketing import resolve_location_bucket


class LocationBucketingTests(SimpleTestCase):
    def test_prefers_staff_home_over_primary(self):
        known = {1, 2}
        self.assertEqual(
            resolve_location_bucket(
                None,
                staff_primary_location_id=2,
                known_location_ids=known,
                primary_location_id=1,
            ),
            2,
        )

    def test_falls_back_to_primary(self):
        known = {1, 2}
        self.assertEqual(
            resolve_location_bucket(
                None,
                staff_primary_location_id=99,
                known_location_ids=known,
                primary_location_id=1,
            ),
            1,
        )
