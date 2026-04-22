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
from decimal import Decimal

from django.core.management.base import BaseCommand

from billing.models import SubscriptionPlan


# Per-currency defaults. USD is the international baseline, MAD is our home
# market. Both can be overridden via env (``STRIPE_PRICE_*``) so finance can
# tune pricing without a deploy.
_PRICING = {
    "USD": {
        "STARTER": {"monthly": Decimal("29"), "yearly": Decimal("290")},
        "GROWTH": {"monthly": Decimal("89"), "yearly": Decimal("890")},
        "ENTERPRISE": {"monthly": Decimal("249"), "yearly": Decimal("2490")},
    },
    "MAD": {
        "STARTER": {"monthly": Decimal("299"), "yearly": Decimal("2990")},
        "GROWTH": {"monthly": Decimal("899"), "yearly": Decimal("8990")},
        "ENTERPRISE": {"monthly": Decimal("2499"), "yearly": Decimal("24990")},
    },
}


# Stable feature keys — the frontend feature-gates against these.
_STARTER_KEYS = [
    "scheduling",
    "timeclock",
    "staff_chat",
    "whatsapp_announce",
    "basic_reports",
]
_GROWTH_KEYS = _STARTER_KEYS + [
    "pos",
    "eatnow",
    "inventory",
    "checklists",
    "prep_list",
    "auto_po",
    "miya",
    "advanced_reports",
]
_ENTERPRISE_KEYS = _GROWTH_KEYS + [
    "multi_location",
    "activity_log",
    "accounting_export",
    "priority_support",
    "custom_integrations",
    "sso",
]


def _features_for(tier: str) -> list[str]:
    """Human-readable bullet list shown on the pricing cards."""
    if tier == "STARTER":
        return [
            "1 location",
            "Up to 10 staff",
            "Smart scheduling + time clock",
            "Staff chat & WhatsApp announcements",
            "Daily / weekly sales reports",
            "Email support",
        ]
    if tier == "GROWTH":
        return [
            "Up to 3 locations",
            "Up to 40 staff",
            "Everything in Starter",
            "POS integrations (Square, Lightspeed)",
            "EatNow reservations + covers forecasting",
            "Inventory, checklists & cleaning",
            "AI prep list + auto-drafted purchase orders",
            "Miya AI assistant on WhatsApp",
            "Priority email & chat support",
        ]
    if tier == "ENTERPRISE":
        return [
            "Unlimited locations & staff",
            "Everything in Growth",
            "Multi-location portfolio dashboard",
            "Role-based permissions & activity audit log",
            "Accounting export (CSV / QuickBooks-ready)",
            "Custom POS / integrations",
            "SSO (Google Workspace)",
            "Dedicated success manager",
            "99.9% uptime SLA",
        ]
    return []


def _env_price_id(tier: str, interval: str) -> str:
    return os.environ.get(f"STRIPE_PRICE_{tier}_{interval.upper()}", "") or ""


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
        table = _PRICING[currency]

        # (tier, slug, name, description, badge, highlight, contact_sales, sort_order, keys)
        specs = [
            (
                "STARTER", "starter", "Starter",
                "Everything a single cafe or bistro needs to run a tight team.",
                "", False, False, 10, _STARTER_KEYS,
                {"max_locations": 1, "max_staff": 10},
            ),
            (
                "GROWTH", "growth", "Growth",
                "Scale to a small chain with AI forecasting, POS, and reservations.",
                "Most popular", True, False, 20, _GROWTH_KEYS,
                {"max_locations": 3, "max_staff": 40},
            ),
            (
                "ENTERPRISE", "enterprise", "Enterprise",
                "Unlimited locations, custom integrations, and white-glove support.",
                "Best value", False, False, 30, _ENTERPRISE_KEYS,
                {"max_locations": None, "max_staff": None},
            ),
        ]

        for tier, slug, name, description, badge, highlight, contact_sales, order, keys, limits in specs:
            prices = table[tier]
            monthly_price_id = _env_price_id(tier, "monthly")
            yearly_price_id = _env_price_id(tier, "yearly")
            effective_contact_sales = contact_sales or not monthly_price_id

            defaults = {
                "name": name,
                "tier": tier,
                "description": description,
                "price": prices["monthly"],
                "price_monthly": prices["monthly"],
                "price_yearly": prices["yearly"],
                "stripe_price_id": monthly_price_id or None,
                "stripe_price_id_monthly": monthly_price_id,
                "stripe_price_id_yearly": yearly_price_id,
                "currency": currency,
                "interval": "month",
                "features": _features_for(tier),
                "feature_keys": keys,
                "max_locations": limits["max_locations"],
                "max_staff": limits["max_staff"],
                "badge": badge,
                "highlight": highlight,
                "cta_label": "Contact sales" if effective_contact_sales else "",
                "sort_order": order,
                "contact_sales": effective_contact_sales,
                "trial_days": 14 if tier != "ENTERPRISE" else 0,
                "is_active": True,
            }

            if dry:
                self.stdout.write(f"[dry] would upsert {slug}: {defaults}")
                continue

            obj, created = SubscriptionPlan.objects.update_or_create(
                slug=slug, defaults=defaults,
            )
            self.stdout.write(self.style.SUCCESS(
                f"{'created' if created else 'updated'} plan {obj.slug} "
                f"({obj.tier}, {obj.currency} {obj.price_monthly}/mo)"
            ))

        if dry:
            self.stdout.write("Dry run complete — no changes written.")
        else:
            self.stdout.write(self.style.SUCCESS("Subscription plans synced."))
