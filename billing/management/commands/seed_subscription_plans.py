"""Seed or refresh the three public subscription tiers.

Idempotent — safe to re-run on every deploy. Stripe price IDs are pulled
from the environment so the same code works in test and live::

    STRIPE_PRICE_STARTER_MONTHLY
    STRIPE_PRICE_STARTER_YEARLY
    STRIPE_PRICE_GROWTH_MONTHLY
    STRIPE_PRICE_GROWTH_YEARLY
    STRIPE_PRICE_ENTERPRISE_MONTHLY
    STRIPE_PRICE_ENTERPRISE_YEARLY

If an env var is missing we still create the row (so the pricing page
renders) but mark its CTA as ``contact_sales=True`` so users can't reach a
broken Stripe checkout.

Usage::

    python manage.py seed_subscription_plans
    python manage.py seed_subscription_plans --currency MAD
"""
from __future__ import annotations

import os

from django.core.management.base import BaseCommand

from billing.plan_seed import _PRICING, sync_subscription_plans


class Command(BaseCommand):
    help = "Create or update the Starter / Growth / Enterprise subscription plans."

    def add_arguments(self, parser):
        parser.add_argument(
            "--currency", default=os.environ.get("BILLING_CURRENCY", "USD"),
            help="Currency code for the seeded prices (USD or MAD). Default: USD.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Print what would be created/updated without touching the DB.",
        )

    def handle(self, *args, **opts):
        currency = opts["currency"].upper()
        if currency not in _PRICING:
            self.stderr.write(self.style.ERROR(
                f"Unknown currency '{currency}'. Supported: {', '.join(_PRICING)}"
            ))
            return

        dry = opts["dry_run"]
        if dry:
            results = sync_subscription_plans(currency=currency, dry_run=True)
            for slug, _ in results:
                self.stdout.write(f"[dry] would upsert {slug}")
            self.stdout.write("Dry run complete — no changes written.")
            return

        results = sync_subscription_plans(currency=currency)
        for slug, created in results:
            self.stdout.write(self.style.SUCCESS(
                f"{'created' if created else 'updated'} plan {slug}"
            ))
        self.stdout.write(self.style.SUCCESS("Subscription plans synced."))
