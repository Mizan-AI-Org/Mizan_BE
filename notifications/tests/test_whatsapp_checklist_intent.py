"""Tests for WhatsApp start-checklist intent detection."""

from django.test import SimpleTestCase

from notifications.views import _normalize_start_checklist_intent


class WhatsAppChecklistIntentTests(SimpleTestCase):
    def test_start_my_checklist(self):
        self.assertTrue(_normalize_start_checklist_intent("Start my checklist"))

    def test_start_checklist_lowercase(self):
        self.assertTrue(_normalize_start_checklist_intent("start checklist"))

    def test_unrelated_not_checklist(self):
        self.assertFalse(_normalize_start_checklist_intent("what are my tasks today"))
