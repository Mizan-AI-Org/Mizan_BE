from django.test import SimpleTestCase

from scheduling.standing_checklist import ADHOC_CHECKLIST_MARKER


class StandingChecklistConstantsTests(SimpleTestCase):
    def test_marker(self):
        self.assertIn("ADHOC", ADHOC_CHECKLIST_MARKER)
