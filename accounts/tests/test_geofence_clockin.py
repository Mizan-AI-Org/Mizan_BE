"""Geofence / clock-in location matching tests."""

from django.test import TestCase

from accounts.models import BusinessLocation, Restaurant
from accounts.utils import (
    find_matching_location,
    location_contains_point,
    point_in_polygon,
    restaurant_has_clockin_geofence,
)


class PointInPolygonTests(TestCase):
    def test_square_contains_center(self):
        # [lat, lon] ring around (32.5, -7.9)
        poly = [
            [32.49, -7.91],
            [32.51, -7.91],
            [32.51, -7.89],
            [32.49, -7.89],
        ]
        self.assertTrue(point_in_polygon(32.50, -7.90, poly))

    def test_square_excludes_outside(self):
        poly = [
            [32.49, -7.91],
            [32.51, -7.91],
            [32.51, -7.89],
            [32.49, -7.89],
        ]
        self.assertFalse(point_in_polygon(33.0, -7.90, poly))

    def test_dict_vertices_accepted(self):
        poly = [
            {"lat": 0.0, "lng": 0.0},
            {"lat": 0.0, "lng": 1.0},
            {"lat": 1.0, "lng": 1.0},
            {"lat": 1.0, "lng": 0.0},
        ]
        self.assertTrue(point_in_polygon(0.5, 0.5, poly))


class FindMatchingLocationTests(TestCase):
    def setUp(self):
        self.rest = Restaurant.objects.create(
            name="Test Cafe",
            latitude=None,
            longitude=None,
            radius=100,
            geofence_enabled=True,
        )

    def test_radius_match_and_miss(self):
        BusinessLocation.objects.create(
            restaurant=self.rest,
            name="Main",
            latitude=32.234567,
            longitude=-7.950000,
            radius=50,
            geofence_enabled=True,
            is_primary=True,
            is_active=True,
        )
        # ~0m from pin
        match, dist, nearest = find_matching_location(self.rest, 32.234567, -7.950000)
        self.assertIsNotNone(match)
        self.assertEqual(match.name, "Main")
        self.assertIsNotNone(dist)
        self.assertLess(dist, 5)

        # Far away
        match2, dist2, nearest2 = find_matching_location(self.rest, 33.0, -8.0)
        self.assertIsNone(match2)
        self.assertIsNotNone(nearest2)
        self.assertGreater(dist2, 1000)

    def test_polygon_match_overrides_radius(self):
        # Pin far from user, but polygon covers user position.
        BusinessLocation.objects.create(
            restaurant=self.rest,
            name="Poly Site",
            latitude=32.0,
            longitude=-8.0,
            radius=5,  # tiny circle around pin — user not in it
            geofence_enabled=True,
            geofence_polygon=[
                [32.49, -7.91],
                [32.51, -7.91],
                [32.51, -7.89],
                [32.49, -7.89],
            ],
            is_primary=True,
            is_active=True,
        )
        match, _, _ = find_matching_location(self.rest, 32.50, -7.90)
        self.assertIsNotNone(match)
        self.assertEqual(match.name, "Poly Site")

    def test_multi_branch_any_zone(self):
        BusinessLocation.objects.create(
            restaurant=self.rest,
            name="A",
            latitude=32.0,
            longitude=-8.0,
            radius=40,
            geofence_enabled=True,
            is_primary=True,
            is_active=True,
        )
        BusinessLocation.objects.create(
            restaurant=self.rest,
            name="B",
            latitude=33.5,
            longitude=-7.5,
            radius=40,
            geofence_enabled=True,
            is_primary=False,
            is_active=True,
        )
        match, _, _ = find_matching_location(self.rest, 33.5, -7.5)
        self.assertIsNotNone(match)
        self.assertEqual(match.name, "B")

    def test_restaurant_has_geofence_from_branch_only(self):
        self.assertFalse(restaurant_has_clockin_geofence(self.rest))
        BusinessLocation.objects.create(
            restaurant=self.rest,
            name="Only Branch",
            latitude=32.1,
            longitude=-7.9,
            radius=100,
            geofence_enabled=True,
            is_primary=True,
            is_active=True,
        )
        self.assertTrue(restaurant_has_clockin_geofence(self.rest))

    def test_location_contains_point_radius(self):
        loc = BusinessLocation(
            name="X",
            latitude=32.0,
            longitude=-8.0,
            radius=100,
            geofence_enabled=True,
            geofence_polygon=[],
        )
        self.assertTrue(location_contains_point(loc, 32.0, -8.0))
        self.assertFalse(location_contains_point(loc, 33.0, -8.0))
