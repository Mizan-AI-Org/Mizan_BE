"""Tests for manager reminder vs dashboard task routing."""
from __future__ import annotations

from django.test import SimpleTestCase

from miya.services.manager_reminder_intent import looks_like_manager_reminder_intent


class ManagerReminderIntentTests(SimpleTestCase):
    def test_remind_me_insurance(self):
        self.assertTrue(
            looks_like_manager_reminder_intent(
                "Remind me 2 weeks before expiry so I can renew my insurance"
            )
        )

    def test_rappelle_moi_french(self):
        self.assertTrue(
            looks_like_manager_reminder_intent(
                "Rappelle-moi de renouveler l'assurance avant expiration"
            )
        )

    def test_staff_delegation_not_reminder(self):
        self.assertFalse(
            looks_like_manager_reminder_intent("Tell Ahmed to prep 10 plates by 5pm")
        )

    def test_assign_staff_not_reminder(self):
        self.assertFalse(
            looks_like_manager_reminder_intent("Assign Karim to clean the fryer tomorrow")
        )
