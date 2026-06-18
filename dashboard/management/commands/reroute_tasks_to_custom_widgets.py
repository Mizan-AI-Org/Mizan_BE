"""Re-route existing Miya tasks into custom widgets by keyword/title match."""

from django.core.management.base import BaseCommand

from dashboard.custom_widget_routing import match_custom_widget_for_task
from dashboard.models import Task


class Command(BaseCommand):
    help = (
        "Assign custom_widget on open Miya tasks when title/description matches "
        "a custom tile's routing_keywords or title."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--restaurant-id",
            dest="restaurant_id",
            help="Limit to one restaurant UUID",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print matches without saving",
        )

    def handle(self, *args, **options):
        qs = Task.objects.filter(source="MIYA", custom_widget__isnull=True).select_related(
            "restaurant", "assigned_to"
        )
        restaurant_id = options.get("restaurant_id")
        if restaurant_id:
            qs = qs.filter(restaurant_id=restaurant_id)

        updated = 0
        for task in qs.iterator():
            widget = match_custom_widget_for_task(
                user=None,
                restaurant=task.restaurant,
                title=task.title or "",
                description=task.description or "",
                source_text=task.ai_summary or "",
            )
            if widget is None:
                continue
            if options.get("dry_run"):
                self.stdout.write(
                    f"would route task {task.id} ({task.title!r}) -> {widget.title!r}"
                )
                updated += 1
                continue
            task.custom_widget = widget
            task.save(update_fields=["custom_widget"])
            self.stdout.write(
                self.style.SUCCESS(
                    f"routed task {task.id} ({task.title!r}) -> {widget.title!r}"
                )
            )
            updated += 1

        self.stdout.write(self.style.SUCCESS(f"Done — {updated} task(s) matched"))
