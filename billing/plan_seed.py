"""Shared subscription-plan seed data and sync logic.

Used by the management command, deploy startup, and data migrations so
production always has Starter / Growth / Enterprise rows for the pricing UI.
"""
from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

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


def _env_price_id(tier: str, interval: str, env: dict[str, str] | None = None) -> str:
    source = env if env is not None else os.environ
    return source.get(f"STRIPE_PRICE_{tier}_{interval.upper()}", "") or ""


def sync_subscription_plans(
    *,
    currency: str = "USD",
    env: dict[str, str] | None = None,
    dry_run: bool = False,
) -> list[tuple[str, bool]]:
    """Create or update the three public tiers. Returns [(slug, created), ...]."""
    from billing.models import SubscriptionPlan

    currency = currency.upper()
    if currency not in _PRICING:
        raise ValueError(f"Unknown currency '{currency}'. Supported: {', '.join(_PRICING)}")

    table = _PRICING[currency]
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

    results: list[tuple[str, bool]] = []
    for tier, slug, name, description, badge, highlight, contact_sales, order, keys, limits in specs:
        prices = table[tier]
        monthly_price_id = _env_price_id(tier, "monthly", env)
        yearly_price_id = _env_price_id(tier, "yearly", env)
        effective_contact_sales = contact_sales or not monthly_price_id

        defaults: dict[str, Any] = {
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

        if dry_run:
            results.append((slug, False))
            continue

        obj, created = SubscriptionPlan.objects.update_or_create(
            slug=slug, defaults=defaults,
        )
        results.append((obj.slug, created))

    return results
