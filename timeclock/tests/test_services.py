"""Smoke tests for the clock-event scoping helpers.

These tests run against the real DB so we can verify the QuerySet
predicate composes correctly. They use the Django ``TestCase`` because
``SimpleTestCase`` doesn't allow DB access.
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import (
    BusinessLocation,
    CustomUser,
    Restaurant,
    StaffRestaurantLink,
)
from timeclock.models import ClockEvent
from timeclock.services import (
    clock_events_for_restaurant_qs,
    restaurant_ids_for_clock_event,
)


class ClockEventScopingTests(TestCase):
    """Make sure the dashboard widgets see EVERY clock-in that belongs
    to the manager's restaurant — including the three categories that
    the old ``staff__restaurant=R`` filter silently dropped:

    1. Branch-level clock-ins (event.location.restaurant=R) where the
       staff's primary restaurant FK is a sibling tenant or NULL.
    2. Multi-restaurant staff working via StaffRestaurantLink.
    3. Legacy events without a ``location`` whose staff's primary FK
       still matches.
    """

    @classmethod
    def setUpTestData(cls):
        cls.rest_a = Restaurant.objects.create(name="Restaurant A", email="a@test.local")
        cls.rest_b = Restaurant.objects.create(name="Restaurant B", email="b@test.local")

        cls.branch_a = BusinessLocation.objects.create(
            restaurant=cls.rest_a, name="A · Main"
        )
        cls.branch_b = BusinessLocation.objects.create(
            restaurant=cls.rest_b, name="B · Main"
        )

        # Staff whose primary restaurant is A.
        cls.alice = CustomUser.objects.create_user(
            email="alice@example.com",
            password="x",
            first_name="Alice",
            last_name="A",
            role="WAITER",
            restaurant=cls.rest_a,
        )
        # Staff whose primary restaurant is B but who also works at A
        # via a secondary link — the multi-restaurant case.
        cls.bob = CustomUser.objects.create_user(
            email="bob@example.com",
            password="x",
            first_name="Bob",
            last_name="B",
            role="WAITER",
            restaurant=cls.rest_b,
        )
        StaffRestaurantLink.objects.create(
            user=cls.bob, restaurant=cls.rest_a, role="WAITER", is_active=True
        )
        # Staff with NO primary restaurant FK — legacy import / orphan.
        cls.carol = CustomUser.objects.create_user(
            email="carol@example.com",
            password="x",
            first_name="Carol",
            last_name="C",
            role="WAITER",
            restaurant=None,
        )

        now = timezone.now()
        # Alice clocks in at branch A (her home) — easy case.
        cls.evt_alice = ClockEvent.objects.create(
            staff=cls.alice, event_type="in", location=cls.branch_a
        )
        # Bob (primary=B) clocks in at branch A. Should belong to A.
        cls.evt_bob_at_a = ClockEvent.objects.create(
            staff=cls.bob, event_type="in", location=cls.branch_a
        )
        # Carol (primary=NULL) clocks in at branch A. Should belong to A
        # via the location signal.
        cls.evt_carol_at_a = ClockEvent.objects.create(
            staff=cls.carol, event_type="in", location=cls.branch_a
        )
        # Bob also clocks in at his home branch B.
        cls.evt_bob_at_b = ClockEvent.objects.create(
            staff=cls.bob, event_type="in", location=cls.branch_b
        )
        # Legacy event — no location, primary FK only. Belongs to A.
        cls.evt_legacy_alice = ClockEvent.objects.create(
            staff=cls.alice, event_type="in", location=None
        )
        # Legacy event — no location, staff with primary=B but secondary
        # link to A. Should appear in BOTH dashboards.
        cls.evt_legacy_bob = ClockEvent.objects.create(
            staff=cls.bob, event_type="in", location=None
        )

    def test_branch_event_scopes_by_location_restaurant(self):
        """Clock-ins recorded at a branch belong to that branch's
        restaurant, regardless of the staff's primary restaurant."""
        ids = set(
            clock_events_for_restaurant_qs(self.rest_a)
            .values_list("id", flat=True)
        )
        self.assertIn(self.evt_alice.id, ids)
        self.assertIn(self.evt_bob_at_a.id, ids)  # primary=B, but at A
        self.assertIn(self.evt_carol_at_a.id, ids)  # primary=NULL, at A
        self.assertNotIn(self.evt_bob_at_b.id, ids)  # at the other branch

    def test_multi_restaurant_staff_via_link_when_no_location(self):
        """A legacy clock-in (no location) by a multi-restaurant staff
        member must appear in BOTH restaurants where they have an
        active link."""
        ids_a = set(
            clock_events_for_restaurant_qs(self.rest_a)
            .values_list("id", flat=True)
        )
        ids_b = set(
            clock_events_for_restaurant_qs(self.rest_b)
            .values_list("id", flat=True)
        )
        self.assertIn(self.evt_legacy_bob.id, ids_a)  # via secondary link
        self.assertIn(self.evt_legacy_bob.id, ids_b)  # via primary FK

    def test_legacy_event_scopes_by_primary_restaurant(self):
        """Legacy event without a location and only a primary FK still
        belongs to the primary restaurant."""
        ids_a = set(
            clock_events_for_restaurant_qs(self.rest_a)
            .values_list("id", flat=True)
        )
        self.assertIn(self.evt_legacy_alice.id, ids_a)

    def test_inactive_link_does_not_leak(self):
        """An inactive StaffRestaurantLink must NOT pull events into
        the unrelated restaurant — otherwise a former employee would
        keep populating their old workplace's widgets."""
        StaffRestaurantLink.objects.filter(user=self.bob, restaurant=self.rest_a).update(
            is_active=False
        )
        ids_a = set(
            clock_events_for_restaurant_qs(self.rest_a)
            .values_list("id", flat=True)
        )
        # Branch event still belongs to A (location-based, the strongest signal).
        self.assertIn(self.evt_bob_at_a.id, ids_a)
        # But the legacy no-location event no longer leaks into A.
        self.assertNotIn(self.evt_legacy_bob.id, ids_a)

    def test_event_type_filter(self):
        ClockEvent.objects.create(
            staff=self.alice, event_type="out", location=self.branch_a
        )
        ins = clock_events_for_restaurant_qs(self.rest_a, event_type="in").count()
        outs = clock_events_for_restaurant_qs(self.rest_a, event_type="out").count()
        self.assertGreaterEqual(ins, 4)
        self.assertEqual(outs, 1)

    def test_date_filter(self):
        old_when = timezone.now() - timedelta(days=2)
        old = ClockEvent.objects.create(
            staff=self.alice, event_type="in", location=self.branch_a
        )
        # Manually backdate to bypass auto_now_add.
        ClockEvent.objects.filter(id=old.id).update(timestamp=old_when)

        today = timezone.now().date()
        today_ids = set(
            clock_events_for_restaurant_qs(self.rest_a, date=today)
            .values_list("id", flat=True)
        )
        self.assertNotIn(old.id, today_ids)

    def test_none_restaurant_returns_empty(self):
        self.assertEqual(
            clock_events_for_restaurant_qs(None).count(), 0
        )

    def test_restaurant_ids_for_event_fans_out(self):
        rids = restaurant_ids_for_clock_event(self.evt_bob_at_a)
        # Branch (A) + primary (B) + secondary link (A) → {A, B}.
        self.assertIn(self.rest_a.id, rids)
        self.assertIn(self.rest_b.id, rids)

    def test_restaurant_ids_for_legacy_event(self):
        rids = restaurant_ids_for_clock_event(self.evt_legacy_alice)
        self.assertEqual(rids, {self.rest_a.id})
