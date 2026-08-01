"""Archive staff daily progress for past dates (manager accountability backfill)."""

from datetime import datetime, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import Restaurant
from dashboard.services.staff_daily_progress import snapshot_staff_daily_progress


class Command(BaseCommand):
    help = (
        "Persist StaffDailyProgressReport rows for one or more past dates. "
        "Use after deploying the archive feature or to recover missed nightly snapshots."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            dest="date",
            help="Single date YYYY-MM-DD to snapshot",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=0,
            help="Snapshot each of the last N days (excluding today)",
        )
        parser.add_argument(
            "--restaurant-id",
            dest="restaurant_id",
            help="Limit to one restaurant UUID",
        )

    def handle(self, *args, **options):
        today = timezone.localdate()
        dates: list = []

        if options.get("date"):
            try:
                dates.append(datetime.strptime(options["date"], "%Y-%m-%d").date())
            except ValueError:
                self.stderr.write(self.style.ERROR("Invalid --date; use YYYY-MM-DD"))
                return

        days = max(0, int(options.get("days") or 0))
        if days:
            for offset in range(1, days + 1):
                dates.append(today - timedelta(days=offset))

        if not dates:
            self.stderr.write(
                self.style.ERROR("Provide --date YYYY-MM-DD and/or --days N")
            )
            return

        dates = sorted({d for d in dates if d < today})
        if not dates:
            self.stderr.write(self.style.WARNING("No past dates to snapshot"))
            return

        restaurants = Restaurant.objects.all()
        restaurant_id = options.get("restaurant_id")
        if restaurant_id:
            restaurants = restaurants.filter(id=restaurant_id)

        total_rows = 0
        for on_date in dates:
            day_rows = 0
            for restaurant in restaurants.iterator(chunk_size=50):
                day_rows += snapshot_staff_daily_progress(restaurant, on_date)
            total_rows += day_rows
            self.stdout.write(
                self.style.SUCCESS(f"{on_date}: saved {day_rows} staff row(s)")
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Done — {len(dates)} day(s), {total_rows} staff row(s) total"
            )
        )
