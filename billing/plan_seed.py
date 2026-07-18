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
    "EUR": {
        "STARTER": {"monthly": Decimal("29"), "yearly": Decimal("290")},
        "GROWTH": {"monthly": Decimal("89"), "yearly": Decimal("890")},
        "ENTERPRISE": {"monthly": Decimal("249"), "yearly": Decimal("2490")},
    },
}

# Country ISO → billing currency when restaurant.currency is missing/unsupported.
_COUNTRY_CURRENCY = {
    "MA": "MAD",
    "US": "USD",
    "GB": "USD",  # priced in USD until GBP table exists
    "FR": "EUR",
    "BE": "EUR",
    "DE": "EUR",
    "ES": "EUR",
    "IT": "EUR",
    "NL": "EUR",
    "PT": "EUR",
    "SN": "MAD",  # regional ops often settle like MA; override via restaurant.currency
}


def supported_billing_currencies() -> tuple[str, ...]:
    return tuple(_PRICING.keys())


def resolve_billing_currency(
    *,
    restaurant=None,
    currency: str | None = None,
    country_code: str | None = None,
) -> str:
    """Pick display/checkout currency for a tenant.

    Preference (location-first):
      1. explicit ``currency`` query/arg
      2. country → currency map (tenant location)
      3. restaurant.currency when it is a supported billing currency
         *and* not a generic USD default that conflicts with the country
      4. settings.BILLING_CURRENCY → USD
    """
    from django.conf import settings

    if restaurant is not None:
        country_code = country_code or getattr(restaurant, "country_code", None)

    country = str(country_code or "").strip().upper()
    country_currency = _COUNTRY_CURRENCY.get(country) if country else None
    restaurant_currency = ""
    if restaurant is not None:
        restaurant_currency = str(getattr(restaurant, "currency", None) or "").strip().upper()

    explicit = str(currency or "").strip().upper()
    if explicit in _PRICING:
        return explicit

    # Location wins over a leftover default USD on the restaurant row.
    if country_currency and country_currency in _PRICING:
        if not restaurant_currency or restaurant_currency == country_currency:
            return country_currency
        if restaurant_currency == "USD" and country_currency != "USD":
            return country_currency
        if restaurant_currency in _PRICING:
            return restaurant_currency
        return country_currency

    if restaurant_currency in _PRICING:
        return restaurant_currency

    fallback = str(getattr(settings, "BILLING_CURRENCY", "USD") or "USD").strip().upper()
    if fallback in _PRICING:
        return fallback
    return "USD"


def localize_plan_payload(plan: dict[str, Any], currency: str) -> dict[str, Any]:
    """Return a plan dict with prices rewritten for ``currency`` (display)."""
    currency = (currency or "USD").upper()
    if currency not in _PRICING:
        return plan
    tier = (plan.get("tier") or "").upper()
    prices = _PRICING[currency].get(tier)
    if not prices:
        return plan
    out = dict(plan)
    out["currency"] = currency
    out["price"] = str(prices["monthly"])
    out["price_monthly"] = str(prices["monthly"])
    out["price_yearly"] = str(prices["yearly"])
    return out

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
            # Only Starter includes the signup trial; Growth/Enterprise are paid as-you-go.
            "trial_days": 14 if tier == "STARTER" else 0,
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
