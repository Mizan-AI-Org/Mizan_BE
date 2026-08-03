"""Unit tests for Miya turn-local working set / pronoun resolution."""

from django.core.cache import cache
from django.test import SimpleTestCase

from miya.services.working_set import (
    apply_working_set_to_args,
    extract_list_entities,
    looks_like_pronoun_ref,
    remember_entities,
    resolve_ids,
)


class WorkingSetTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_pronoun_detection(self):
        self.assertTrue(looks_like_pronoun_ref("les"))
        self.assertTrue(looks_like_pronoun_ref("them"))
        self.assertTrue(looks_like_pronoun_ref("celui-là"))
        self.assertTrue(looks_like_pronoun_ref(""))
        self.assertFalse(looks_like_pronoun_ref("a1b2c3d4-e5f6-7890-abcd-ef1234567890"))

    def test_remember_and_resolve_all(self):
        remember_entities(
            restaurant_id="r1",
            user_id="u1",
            kind="invoices",
            entities=[
                {"id": "inv-1", "label": "Sysco"},
                {"id": "inv-2", "label": "Metro"},
            ],
        )
        ids = resolve_ids(
            restaurant_id="r1",
            user_id="u1",
            kind="invoices",
            all_listed=True,
        )
        self.assertEqual(ids, ["inv-1", "inv-2"])

    def test_resolve_first(self):
        remember_entities(
            restaurant_id="r1",
            user_id="u1",
            kind="invoices",
            entities=[{"id": "a"}, {"id": "b"}, {"id": "c"}],
        )
        ids = resolve_ids(
            restaurant_id="r1",
            user_id="u1",
            kind="invoices",
            pronoun_hint="the first one",
        )
        self.assertEqual(ids, ["a"])

    def test_assign_invoice_fills_from_working_set(self):
        remember_entities(
            restaurant_id="r1",
            user_id="u1",
            kind="invoices",
            entities=[{"id": "inv-9", "label": "Vendor"}],
        )
        args = apply_working_set_to_args(
            "assign_invoice",
            {"staff_name": "Sara", "invoice_ids": ["les"]},
            restaurant_id="r1",
            user_id="u1",
        )
        self.assertEqual(args.get("invoice_ids"), ["inv-9"])

    def test_extract_list_invoices(self):
        kind, entities = extract_list_entities(
            "list_invoices",
            {
                "invoices": [
                    {"id": "1", "vendor_name": "A", "invoice_number": "10"},
                    {"id": "2", "vendor_name": "B"},
                ]
            },
        )
        self.assertEqual(kind, "invoices")
        self.assertEqual([e["id"] for e in entities], ["1", "2"])

    def test_cancel_task_pronoun(self):
        remember_entities(
            restaurant_id="r1",
            user_id="u1",
            kind="tasks",
            entities=[{"id": "task-1", "label": "Pay staff"}],
        )
        args = apply_working_set_to_args(
            "update_dashboard_task_status",
            {"task_id": "it", "status": "CANCELLED"},
            restaurant_id="r1",
            user_id="u1",
        )
        self.assertEqual(args.get("task_id"), "task-1")
