import logging
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

# Use string sender to avoid tight coupling; Django resolves lazily.
@receiver(post_save, sender="accounts.Restaurant")
def create_default_checklists_on_restaurant_create(sender, instance, created, **kwargs):
    """Seed a set of default checklist templates for a newly created Restaurant.

    Idempotent: if templates with the same names already exist for the restaurant,
    no duplicates are created. Steps are only created if the template has none.
    """
    if not created:
        return

    from .models import ChecklistTemplate, ChecklistStep

    logger = logging.getLogger(__name__)

    from django.utils import timezone

    def normalize_duration(value):
        try:
            # Interpret integers as minutes for DurationField
            if isinstance(value, int):
                return timezone.timedelta(minutes=value)
        except Exception:
            pass
        return value

    default_templates = [
        {
            "name": "Restaurant Safety Checklist",
            "category": "SAFETY",
            "estimated_duration": normalize_duration(20),
            "requires_supervisor_approval": True,
            "steps": [
                {"title": "Check fire extinguishers tagged and accessible", "step_type": "CHECK"},
                {"title": "Verify emergency exits unobstructed", "step_type": "CHECK"},
                {"title": "Log walk-in cooler temperature", "step_type": "MEASUREMENT", "measurement_type": "TEMPERATURE", "min_value": 0, "max_value": 10},
                {"title": "Upload photo of safety signage", "step_type": "PHOTO"},
                {"title": "Supervisor signature for safety verification", "step_type": "SIGNATURE"},
            ],
        },
        {
            "name": "Restaurant Opening Checklist",
            "category": "OPENING",
            "estimated_duration": normalize_duration(30),
            "requires_supervisor_approval": False,
            "steps": [
                {"title": "Front-of-house cleanliness check", "step_type": "CHECK"},
                {"title": "Prepare stations and restock essentials", "step_type": "CHECK"},
                {"title": "Record hot-holding unit temperature", "step_type": "MEASUREMENT", "measurement_type": "TEMPERATURE", "min_value": 60, "max_value": 75},
                {"title": "Upload photo of prepared line", "step_type": "PHOTO"},
            ],
        },
        {
            "name": "Restaurant Closing Checklist",
            "category": "CLOSING",
            "estimated_duration": 25,
            "requires_supervisor_approval": False,
            "steps": [
                {"title": "Sanitize prep surfaces and equipment", "step_type": "CHECK"},
                {"title": "Secure inventory and lock storage", "step_type": "CHECK"},
                {"title": "Record walk-in cooler temperature", "step_type": "MEASUREMENT", "measurement_type": "TEMPERATURE", "min_value": 0, "max_value": 10},
                {"title": "Upload photo of cleaned kitchen", "step_type": "PHOTO"},
            ],
        },
        {
            "name": "Restaurant Visitation Report",
            "category": "COMPLIANCE",
            "estimated_duration": 40,
            "requires_supervisor_approval": True,
            "steps": [
                {"title": "Evaluate sanitation SOP compliance", "step_type": "CHECK"},
                {"title": "Review temperature logs completeness", "step_type": "CHECK"},
                {"title": "Attach photo evidence of findings", "step_type": "PHOTO"},
                {"title": "Manager signature acknowledging report", "step_type": "SIGNATURE"},
            ],
        },
        {
            "name": "Restaurant Mystery Shopper Template",
            "category": "QUALITY",
            "estimated_duration": normalize_duration(35),
            "requires_supervisor_approval": False,
            "steps": [
                {"title": "Rate service friendliness", "step_type": "CHECK"},
                {"title": "Assess food quality and presentation", "step_type": "CHECK"},
                {"title": "Upload receipt photo", "step_type": "PHOTO"},
            ],
        },
        {
            "name": "Restaurant Manager Opening Checklist",
            "category": "OPENING",
            "estimated_duration": normalize_duration(30),
            "requires_supervisor_approval": True,
            "steps": [
                {"title": "Verify staff assignments and coverage", "step_type": "CHECK"},
                {"title": "Confirm POS and printers operational", "step_type": "CHECK"},
                {"title": "Record dish machine rinse temperature", "step_type": "MEASUREMENT", "measurement_type": "TEMPERATURE", "min_value": 70, "max_value": 85},
                {"title": "Manager signature for opening readiness", "step_type": "SIGNATURE"},
            ],
        },
    ]

    try:
        with transaction.atomic():
            for tmpl in default_templates:
                template, created_template = ChecklistTemplate.objects.get_or_create(
                    restaurant=instance,
                    name=tmpl["name"],
                    defaults={
                        "category": tmpl["category"],
                        "estimated_duration": normalize_duration(tmpl.get("estimated_duration", 15)),
                        "requires_supervisor_approval": tmpl.get("requires_supervisor_approval", False),
                    },
                )

                if created_template:
                    logger.info(
                        "Created default checklist template '%s' for restaurant %s", tmpl["name"], instance.id
                    )
                else:
                    logger.debug(
                        "Default checklist template '%s' already exists for restaurant %s", tmpl["name"], instance.id
                    )

                # Only seed steps if none exist to avoid duplication.
                if template.steps.count() == 0:
                    order_counter = 1
                    for step in tmpl.get("steps", []):
                        ChecklistStep.objects.create(
                            template=template,
                            title=step["title"],
                            description="",
                            step_type=step.get("step_type", "CHECK"),
                            order=order_counter,
                            is_required=True,
                            measurement_type=step.get("measurement_type"),
                            min_value=step.get("min_value"),
                            max_value=step.get("max_value"),
                            conditional_logic={},
                        )
                        order_counter += 1
                    logger.info(
                        "Seeded %d steps for template '%s' (restaurant %s)",
                        template.steps.count(),
                        template.name,
                        instance.id,
                    )
    except Exception as exc:
        logger.exception("Failed to create default checklists for restaurant %s: %s", instance.id, exc)
