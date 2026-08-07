"""Live board — scheduled processes visible before clock-in."""

from datetime import time

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import CustomUser, Restaurant
from scheduling.models import AssignedShift, WeeklySchedule
from scheduling.task_templates import TaskTemplate


class LiveBoardMetricsTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Live Cafe", email="live@cafe.test")
        self.manager = CustomUser.objects.create_user(
            email="mgr-live@cafe.test",
            password="pass12345",
            first_name="Manager",
            last_name="One",
            role="MANAGER",
            restaurant=self.restaurant,
        )
        self.staff = CustomUser.objects.create_user(
            email="staff-live@cafe.test",
            password="pass12345",
            first_name="Adama",
            last_name="Jarju",
            role="WAITER",
            restaurant=self.restaurant,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.manager)

        schedule = WeeklySchedule.objects.create(
            restaurant=self.restaurant,
            week_start=timezone.localdate(),
            week_end=timezone.localdate(),
        )
        self.template = TaskTemplate.objects.create(
            restaurant=self.restaurant,
            name="Opening Checklist",
            template_type="OPENING",
        )
        self.shift = AssignedShift.objects.create(
            schedule=schedule,
            staff=self.staff,
            shift_date=timezone.localdate(),
            start_time=time(8, 0),
            end_time=time(16, 0),
            role="WAITER",
            status="SCHEDULED",
        )
        self.shift.task_templates.add(self.template)

    def test_live_board_metrics_lists_scheduled_process(self):
        resp = self.client.get("/dashboard/analytics/live_board_metrics/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["active_processes_count"], 1)
        self.assertEqual(len(data["active_processes"]), 1)
        self.assertEqual(data["active_processes"][0]["process_name"], "Opening Checklist")
        self.assertEqual(data["active_processes"][0]["staff"][0]["name"], "Adama Jarju")

    def test_staff_live_metrics_shows_scheduled_process_before_clock_in(self):
        resp = self.client.get("/dashboard/analytics/staff_live_metrics/")
        self.assertEqual(resp.status_code, 200)
        rows = resp.json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["shift_status"], "SCHEDULED")
        self.assertEqual(rows[0]["current_process"]["name"], "Opening Checklist")
        self.assertEqual(rows[0]["current_process"]["progress"], 0)
