"""Tests for process template document import."""
from django.test import TestCase

from accounts.models import CustomUser, Restaurant
from scheduling.process_template_import_service import (
    bulk_create_task_templates,
    parse_process_templates_from_bytes,
)
from scheduling.task_templates import TaskTemplate


class ProcessTemplateImportTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Import Test")
        self.manager = CustomUser.objects.create_user(
            email="mgr@import.test",
            password="testpass123",
            first_name="Mgr",
            last_name="Test",
            role="MANAGER",
            restaurant=self.restaurant,
        )

    def test_csv_import_parses_process_and_tasks(self):
        csv_text = (
            "process_name,task_title\n"
            "Runner Opening,Unlock front door\n"
            "Runner Opening,Turn on lights\n"
            "Closing Checklist,Lock doors\n"
        ).encode("utf-8")
        parsed = parse_process_templates_from_bytes(
            csv_text, content_type="text/csv", name="processes.csv",
        )
        self.assertEqual(len(parsed["templates"]), 2)
        names = {t["name"] for t in parsed["templates"]}
        self.assertIn("Runner Opening", names)
        self.assertIn("Closing Checklist", names)

    def test_bulk_create_skips_duplicate_names(self):
        TaskTemplate.objects.create(
            restaurant=self.restaurant,
            name="Runner Opening",
            template_type="OPENING",
            tasks=[{"id": "1", "title": "Existing", "priority": "MEDIUM", "completed": False}],
        )
        templates = [
            {
                "name": "Runner Opening",
                "template_type": "OPENING",
                "tasks": [{"id": "2", "title": "New step", "priority": "MEDIUM", "completed": False}],
            },
            {
                "name": "Brand New Process",
                "template_type": "CUSTOM",
                "tasks": [{"id": "3", "title": "Do thing", "priority": "MEDIUM", "completed": False}],
            },
        ]
        result = bulk_create_task_templates(
            self.restaurant, templates, acting_user=self.manager, skip_duplicates=True,
        )
        self.assertEqual(len(result["created"]), 1)
        self.assertEqual(result["created"][0]["name"], "Brand New Process")
        self.assertEqual(len(result["skipped"]), 1)
