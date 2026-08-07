"""Stale open clock-in cleanup."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import CustomUser, Restaurant
from scheduling.models import AssignedShift, WeeklySchedule
from timeclock.models import ClockEvent
from timeclock.stale_sessions import close_stale_open_clock_in, is_open_clock_in_active


class StaleClockInTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(
            name="Stale Resto",
            slug="stale-resto",
            automatic_clock_out=True,
        )
        self.staff = CustomUser.objects.create_user(
            email="adama@test.com",
            password="pass",
            role="STAFF",
            restaurant=self.restaurant,
            phone="+212600000099",
            first_name="Adama",
        )
        today = timezone.localdate()
        self.schedule = WeeklySchedule.objects.create(
            restaurant=self.restaurant,
            week_start=today,
            week_end=today + timedelta(days=6),
            is_published=True,
        )

    def test_closes_open_session_after_shift_end(self):
        now = timezone.now()
        AssignedShift.objects.create(
            schedule=self.schedule,
            staff=self.staff,
            shift_date=timezone.localdate(),
            start_time=now - timedelta(hours=8),
            end_time=now - timedelta(hours=1),
            status="IN_PROGRESS",
            role="STAFF",
        )
        ClockEvent.objects.create(
            staff=self.staff,
            event_type="in",
            device_id="test",
        )
        closed, out_event = close_stale_open_clock_in(
            self.staff,
            now=now,
            source="test",
        )
        self.assertTrue(closed)
        self.assertIsNotNone(out_event)
        self.assertFalse(is_open_clock_in_active(self.staff, now=now))

    def test_keeps_active_same_day_session(self):
        now = timezone.now()
        AssignedShift.objects.create(
            schedule=self.schedule,
            staff=self.staff,
            shift_date=timezone.localdate(),
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=3),
            status="IN_PROGRESS",
            role="STAFF",
        )
        ClockEvent.objects.create(
            staff=self.staff,
            event_type="in",
            device_id="test",
        )
        closed, _ = close_stale_open_clock_in(self.staff, now=now, source="test")
        self.assertFalse(closed)
        self.assertTrue(is_open_clock_in_active(self.staff, now=now))
