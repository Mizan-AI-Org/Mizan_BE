"""Checklist photo proof — template flags → ShiftTask → Yes handler."""

from django.test import TestCase

from scheduling.checklist_photo import (
    apply_verification_fields_to_shift_task,
    task_requires_photo,
    verification_fields_from_item,
)
from scheduling.models import AssignedShift, ShiftTask, WeeklySchedule
from scheduling.shift_auto_templates import _normalize_task_item
from accounts.models import CustomUser, Restaurant
from django.utils import timezone


class ChecklistPhotoFlagTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Photo Cafe", email="p@cafe.test")
        self.staff = CustomUser.objects.create_user(
            email="staff-photo@cafe.test",
            password="pass12345",
            role="WAITER",
            restaurant=self.restaurant,
        )
        schedule = WeeklySchedule.objects.create(
            restaurant=self.restaurant,
            week_start=timezone.localdate(),
            week_end=timezone.localdate(),
        )
        self.shift = AssignedShift.objects.create(
            schedule=schedule,
            staff=self.staff,
            shift_date=timezone.localdate(),
            role="WAITER",
            status="IN_PROGRESS",
        )

    def test_verification_fields_from_template_item(self):
        fields = verification_fields_from_item(
            {"title": "Clean machines", "requires_photo": True}
        )
        self.assertTrue(fields["requires_photo"])
        self.assertEqual(fields["verification_type"], "PHOTO")

    def test_normalize_task_item_preserves_requires_photo(self):
        item = _normalize_task_item(
            {"title": "Descale coffee", "requires_photo": True, "response_type": "yes_no"}
        )
        self.assertTrue(item["requires_photo"])
        self.assertEqual(item["verification_type"], "PHOTO")

    def test_shift_task_branch_config_triggers_photo_after_yes(self):
        task = ShiftTask.objects.create(
            shift=self.shift,
            title="Clean and descale coffee machines",
            assigned_to=self.staff,
            status="TODO",
            branch_config={"requires_photo": True, "verification_type": "PHOTO"},
            verification_required=True,
            verification_type="PHOTO",
        )
        self.assertTrue(task_requires_photo(task))

    def test_apply_verification_fields_upgrades_legacy_task(self):
        task = ShiftTask.objects.create(
            shift=self.shift,
            title="Service refrigeration units",
            assigned_to=self.staff,
            status="TODO",
            branch_config={},
            verification_required=False,
            verification_type="NONE",
        )
        self.assertFalse(task_requires_photo(task))
        updated = apply_verification_fields_to_shift_task(
            task,
            {"title": "Service refrigeration units", "requires_photo": True},
        )
        self.assertTrue(updated)
        task.refresh_from_db()
        self.assertTrue(task_requires_photo(task))
