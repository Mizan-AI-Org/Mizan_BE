"""Backfill personal reminders from compliance documents."""
from django.core.management.base import BaseCommand

from accounts.models import Restaurant
from payroll.services.compliance_reminder_sync import sync_all_compliance_reminders_for_restaurant


class Command(BaseCommand):
    help = "Sync compliance document expiry dates to Meetings & Reminders personal reminder rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--restaurant-id",
            type=str,
            default="",
            help="Limit to one restaurant UUID (default: all active restaurants).",
        )

    def handle(self, *args, **options):
        rid = (options.get("restaurant_id") or "").strip()
        qs = Restaurant.objects.all()
        if rid:
            qs = qs.filter(id=rid)
        totals = {"created": 0, "updated": 0, "cancelled": 0, "skipped": 0}
        for restaurant in qs.iterator():
            row = sync_all_compliance_reminders_for_restaurant(restaurant)
            for k in totals:
                totals[k] += row.get(k, 0)
            self.stdout.write(
                f"{restaurant.name}: +{row['created']} created, {row['updated']} updated"
            )
        self.stdout.write(self.style.SUCCESS(f"Done — {totals}"))
