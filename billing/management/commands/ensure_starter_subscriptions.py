"""Backfill incomplete subscriptions onto Starter trial."""
from django.core.management.base import BaseCommand

from accounts.models import Restaurant
from billing.services import ensure_starter_subscription


class Command(BaseCommand):
    help = "Ensure every tenant has a Starter trial subscription (fixes incomplete rows)."

    def handle(self, *args, **options):
        fixed = 0
        for restaurant in Restaurant.objects.all().iterator():
            before = getattr(getattr(restaurant, "subscription", None), "status", None)
            sub = ensure_starter_subscription(restaurant)
            if before != sub.status or before == "incomplete":
                fixed += 1
                self.stdout.write(f"  {restaurant.name}: {before} → {sub.status}")
        self.stdout.write(self.style.SUCCESS(f"Ensured starter subscriptions ({fixed} touched)"))
