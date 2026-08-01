"""Staff daily progress — live vs archived snapshots."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import CustomUser, Restaurant
from dashboard.models import StaffDailyProgressReport, Task
from dashboard.services.staff_daily_progress import (
    close_stale_shift_checklists,
    compute_staff_daily_progress,
    snapshot_staff_daily_progress,
    staff_has_today_live_activity,
)


class StaffDailyProgressTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(
            name="Progress Cafe",
            email="progress@cafe.test",
        )
        self.manager = CustomUser.objects.create_user(
            email="mgr@cafe.test",
            password="pass12345",
            first_name="M",
            last_name="Gr",
            role="MANAGER",
            restaurant=self.restaurant,
        )
        self.staff = CustomUser.objects.create_user(
            email="staff@cafe.test",
            password="pass12345",
            first_name="Adama",
            last_name="Jarju",
            role="WAITER",
            restaurant=self.restaurant,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.manager)

    def test_live_excludes_stale_open_tasks(self):
        """Open tasks from prior days must not appear in today's live widget."""
        task = Task.objects.create(
            restaurant=self.restaurant,
            assigned_to=self.staff,
            title="Stale open",
            status="PENDING",
        )
        Task.objects.filter(pk=task.pk).update(
            created_at=timezone.now() - timedelta(days=3)
        )
        rows = compute_staff_daily_progress(self.restaurant)
        self.assertEqual(rows, [])

    def test_live_includes_today_tasks(self):
        Task.objects.create(
            restaurant=self.restaurant,
            assigned_to=self.staff,
            title="Today task",
            status="PENDING",
        )
        rows = compute_staff_daily_progress(self.restaurant)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["total"], 1)

    def test_snapshot_and_history_api(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        task = Task.objects.create(
            restaurant=self.restaurant,
            assigned_to=self.staff,
            title="Yesterday",
            status="COMPLETED",
            due_date=yesterday,
        )
        Task.objects.filter(pk=task.pk).update(
            created_at=timezone.now() - timedelta(days=1)
        )
        count = snapshot_staff_daily_progress(self.restaurant, yesterday)
        self.assertEqual(count, 1)

        resp = self.client.get(
            f"/api/dashboard/staff-daily-progress/?date={yesterday.isoformat()}"
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body.get("archived"))
        self.assertEqual(body["date"], str(yesterday))

        hist = self.client.get("/api/dashboard/staff-daily-progress/history/?days=7")
        self.assertEqual(hist.status_code, 200)
        self.assertTrue(hist.json().get("success"))

    def test_staff_has_today_live_activity_excludes_stale_checklist(self):
        from scheduling.models import AssignedShift, ShiftChecklistProgress, WeeklySchedule

        schedule = WeeklySchedule.objects.create(
            restaurant=self.restaurant,
            week_start=timezone.localdate(),
            week_end=timezone.localdate() + timedelta(days=6),
        )
        shift = AssignedShift.objects.create(
            schedule=schedule,
            staff=self.staff,
            shift_date=timezone.localdate() - timedelta(days=2),
            role="WAITER",
            status="IN_PROGRESS",
        )
        ShiftChecklistProgress.objects.create(
            shift=shift,
            staff=self.staff,
            status="IN_PROGRESS",
            task_ids=["t1"],
        )
        self.assertFalse(staff_has_today_live_activity(self.restaurant, self.staff))

        closed = close_stale_shift_checklists(restaurant=self.restaurant)
        self.assertEqual(closed, 1)
