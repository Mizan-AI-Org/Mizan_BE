"""Checklist completion archive — responses, photos, compliance."""

from django.test import TestCase
from django.utils import timezone

from accounts.models import CustomUser, Restaurant
from scheduling.checklist_completion import (
    build_checklist_completion_summary,
    finalize_shift_checklist_completion,
)
from scheduling.models import (
    AssignedShift,
    ShiftChecklistProgress,
    ShiftTask,
    TaskVerificationRecord,
    WeeklySchedule,
)


class ChecklistCompletionTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Archive Cafe", email="a@cafe.test")
        self.staff = CustomUser.objects.create_user(
            email="staff@cafe.test",
            password="pass12345",
            first_name="Adama",
            last_name="Jarju",
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
        self.task = ShiftTask.objects.create(
            shift=self.shift,
            title="Cash reconciliation",
            assigned_to=self.staff,
            status="COMPLETED",
            completed_at=timezone.now(),
            branch_config={"requires_photo": True, "verification_type": "PHOTO"},
            verification_required=True,
            verification_type="PHOTO",
        )
        self.prog = ShiftChecklistProgress.objects.create(
            shift=self.shift,
            staff=self.staff,
            task_ids=[str(self.task.id)],
            responses={str(self.task.id): "yes"},
            status="IN_PROGRESS",
        )

    def test_finalize_stores_photos_in_summary(self):
        TaskVerificationRecord.objects.create(
            task=self.task,
            submitted_by=self.staff,
            photo_evidence=[
                {
                    "url": "https://cdn.example/checklist.jpg",
                    "media_id": "m1",
                    "submitted_at": timezone.now().isoformat(),
                }
            ],
            checklist_responses={"response": "yes", "photo_received": True},
        )
        summary = finalize_shift_checklist_completion(self.prog, self.staff)
        self.prog.refresh_from_db()
        self.assertEqual(self.prog.status, "COMPLETED")
        self.assertTrue(self.prog.completion_summary)
        self.assertEqual(summary["tasks"][0]["photo_count"], 1)
        self.assertTrue(summary["fully_compliant"])

    def test_missing_photo_marks_non_compliant(self):
        summary = build_checklist_completion_summary(self.prog, self.staff)
        self.assertFalse(summary["fully_compliant"])
        self.assertEqual(summary["missing_photo_task_ids"], [str(self.task.id)])
