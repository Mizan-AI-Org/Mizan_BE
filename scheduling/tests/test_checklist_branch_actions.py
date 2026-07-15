from django.test import SimpleTestCase

from scheduling.checklist_branch_actions import resolve_branch_action


class ResolveBranchActionTests(SimpleTestCase):
    def test_reads_branch_config(self):
        class T:
            title = "Tesing"
            branch_config = {
                "branches": {
                    "no": {
                        "type": "alert",
                        "message": "Needs attention",
                        "assignees": ["abc"],
                    },
                    "yes": {"type": "next"},
                }
            }
            shift = None

        action = resolve_branch_action(T(), "no")
        self.assertEqual(action["type"], "alert")
        self.assertEqual(action["assignees"], ["abc"])

    def test_missing_returns_none(self):
        class T:
            title = "X"
            branch_config = {}
            shift = type("S", (), {"task_templates": type("M", (), {"all": lambda self: []})()})()

        self.assertIsNone(resolve_branch_action(T(), "no"))
