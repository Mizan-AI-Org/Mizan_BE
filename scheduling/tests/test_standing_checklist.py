"""Standing process assignments — per-staff checklist isolation."""

from datetime import time

from django.test import TestCase
from django.utils import timezone

from accounts.models import CustomUser, Restaurant
from notifications.services import NotificationService
from scheduling.models import AssignedShift, ShiftChecklistProgress, ShiftTask, WeeklySchedule
from scheduling.standing_checklist import (
    ADHOC_CHECKLIST_MARKER,
    ensure_checklist_shift_for_staff,
    get_standing_templates_for_staff,
    user_can_run_template,
)
from scheduling.task_templates import TaskTemplate


class StandingChecklistConstantsTests(TestCase):
    def test_marker(self):
        self.assertIn("ADHOC", ADHOC_CHECKLIST_MARKER)


class StandingChecklistPerStaffTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Standing Cafe", email="s@cafe.test")
        self.manager = CustomUser.objects.create_user(
            email="mgr-standing@cafe.test",
            password="pass12345",
            first_name="Mgr",
            last_name="One",
            role="MANAGER",
            restaurant=self.restaurant,
        )
        self.chef = CustomUser.objects.create_user(
            email="chef@cafe.test",
            password="pass12345",
            first_name="Chef",
            last_name="Ramsey",
            role="CHEF",
            restaurant=self.restaurant,
        )
        self.fatima = CustomUser.objects.create_user(
            email="fatima@cafe.test",
            password="pass12345",
            first_name="Fatima",
            last_name="Zahra",
            role="WAITER",
            restaurant=self.restaurant,
        )
        self.template = TaskTemplate.objects.create(
            restaurant=self.restaurant,
            name="Equipment Maintenance",
            template_type="MAINTENANCE",
            tasks=[
                {"title": "Inspect ovens", "description": "Check seals"},
                {"title": "Log temperatures", "description": ""},
            ],
        )
        self.template.standing_assignees.add(self.chef, self.fatima)

        schedule = WeeklySchedule.objects.create(
            restaurant=self.restaurant,
            week_start=timezone.localdate(),
            week_end=timezone.localdate(),
        )
        self.shared_shift = AssignedShift.objects.create(
            schedule=schedule,
            staff=self.chef,
            shift_date=timezone.localdate(),
            start_time=time(8, 0),
            end_time=time(16, 0),
            role="CHEF",
            status="IN_PROGRESS",
        )
        self.shared_shift.staff_members.add(self.chef, self.fatima)
        self.shared_shift.task_templates.add(self.template)

    def test_standing_templates_resolved_per_user(self):
        chef_tpls = get_standing_templates_for_staff(self.chef)
        self.assertEqual(len(chef_tpls), 1)
        outsider = CustomUser.objects.create_user(
            email="other@cafe.test",
            password="pass12345",
            role="WAITER",
            restaurant=self.restaurant,
        )
        self.assertEqual(get_standing_templates_for_staff(outsider), [])

    def test_user_can_run_template_respects_standing_assignees(self):
        self.assertTrue(user_can_run_template(self.chef, self.template))
        self.assertTrue(user_can_run_template(self.fatima, self.template))
        outsider = CustomUser.objects.create_user(
            email="x@cafe.test",
            password="pass12345",
            role="WAITER",
            restaurant=self.restaurant,
        )
        self.assertFalse(user_can_run_template(outsider, self.template))

    def test_open_template_without_standing_assignees(self):
        open_tpl = TaskTemplate.objects.create(
            restaurant=self.restaurant,
            name="Open shift checklist",
            template_type="CUSTOM",
            tasks=[{"title": "Wipe counters"}],
        )
        self.assertTrue(user_can_run_template(self.chef, open_tpl))

    def test_each_staff_gets_own_tasks_on_shared_shift(self):
        svc = NotificationService()
        svc._ensure_shift_tasks_from_templates(self.chef, self.shared_shift)
        svc._ensure_shift_tasks_from_templates(self.fatima, self.shared_shift)

        chef_tasks = ShiftTask.objects.filter(shift=self.shared_shift, assigned_to=self.chef)
        fatima_tasks = ShiftTask.objects.filter(shift=self.shared_shift, assigned_to=self.fatima)
        self.assertEqual(chef_tasks.count(), 2)
        self.assertEqual(fatima_tasks.count(), 2)
        self.assertNotEqual(
            set(chef_tasks.values_list("id", flat=True)),
            set(fatima_tasks.values_list("id", flat=True)),
        )

    def test_adhoc_shift_is_per_staff_without_roster(self):
        shift_chef = ensure_checklist_shift_for_staff(self.chef, create_adhoc=True)
        shift_fatima = ensure_checklist_shift_for_staff(self.fatima, create_adhoc=True)
        self.assertIsNotNone(shift_chef)
        self.assertIsNotNone(shift_fatima)
        self.assertNotEqual(shift_chef.id, shift_fatima.id)
        self.assertIn(ADHOC_CHECKLIST_MARKER, shift_chef.notes or "")
        self.assertIn(ADHOC_CHECKLIST_MARKER, shift_fatima.notes or "")

    def test_checklist_progress_is_per_staff(self):
        svc = NotificationService()
        svc._ensure_shift_tasks_from_templates(self.chef, self.shared_shift)
        svc._ensure_shift_tasks_from_templates(self.fatima, self.shared_shift)
        chef_task_ids = [
            str(tid)
            for tid in ShiftTask.objects.filter(
                shift=self.shared_shift, assigned_to=self.chef
            ).values_list("id", flat=True)
        ]
        fatima_task_ids = [
            str(tid)
            for tid in ShiftTask.objects.filter(
                shift=self.shared_shift, assigned_to=self.fatima
            ).values_list("id", flat=True)
        ]
        ShiftChecklistProgress.objects.create(
            shift=self.shared_shift,
            staff=self.chef,
            task_ids=chef_task_ids,
            responses={chef_task_ids[0]: "yes"},
            status="IN_PROGRESS",
        )
        ShiftChecklistProgress.objects.create(
            shift=self.shared_shift,
            staff=self.fatima,
            task_ids=fatima_task_ids,
            responses={},
            status="IN_PROGRESS",
        )
        chef_prog = ShiftChecklistProgress.objects.get(shift=self.shared_shift, staff=self.chef)
        fatima_prog = ShiftChecklistProgress.objects.get(shift=self.shared_shift, staff=self.fatima)
        self.assertEqual(len(chef_prog.responses), 1)
        self.assertEqual(len(fatima_prog.responses), 0)
