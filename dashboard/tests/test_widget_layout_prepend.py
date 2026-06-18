from django.test import SimpleTestCase

from dashboard.views_widget_layout import _prepend_widgets_to_order


class PrependWidgetsToOrderTests(SimpleTestCase):
    def test_inserts_new_widgets_at_top_preserving_request_order(self):
        current = ["finance", "operations", "staff_inbox"]
        current, added = _prepend_widgets_to_order(current, ["maintenance", "human_resources"])
        self.assertEqual(added, ["maintenance", "human_resources"])
        self.assertEqual(
            current,
            ["maintenance", "human_resources", "finance", "operations", "staff_inbox"],
        )

    def test_skips_widgets_already_on_dashboard(self):
        current = ["finance", "operations"]
        current, added = _prepend_widgets_to_order(current, ["finance", "maintenance"])
        self.assertEqual(added, ["maintenance"])
        self.assertEqual(current, ["maintenance", "finance", "operations"])

    def test_bubbles_existing_widgets_to_top(self):
        current = ["finance", "operations", "maintenance"]
        current, added = _prepend_widgets_to_order(current, ["finance"])
        self.assertEqual(added, [])
        self.assertEqual(current, ["finance", "operations", "maintenance"])

    def test_empty_add_list_is_noop(self):
        current = ["finance"]
        current, added = _prepend_widgets_to_order(current, [])
        self.assertEqual(added, [])
        self.assertEqual(current, ["finance"])
