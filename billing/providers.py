"""Payment-provider resolution for tenant billing upgrades.

Payment walls are country/tenant-specific. Today Stripe is the only wired
provider; additional providers (and country routing) will plug in here when
the payment-wall details are ready.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from django.conf import settings

from accounts.models import Restaurant

ProviderId = Literal["stripe", "none"]


@dataclass(frozen=True)
class PaymentProviderChoice:
    provider: ProviderId
    configured: bool
    reason: str


# Default country → provider map. Override via settings.PAYMENT_PROVIDER_BY_COUNTRY.
_DEFAULT_COUNTRY_PROVIDERS: dict[str, ProviderId] = {
    # Stripe-friendly markets (extend as you add agreements)
    "US": "stripe",
    "GB": "stripe",
    "CA": "stripe",
    "AU": "stripe",
    "FR": "stripe",
    "DE": "stripe",
    "ES": "stripe",
    "IT": "stripe",
    "NL": "stripe",
    "IE": "stripe",
    "BE": "stripe",
    "PT": "stripe",
    "SE": "stripe",
    "NO": "stripe",
    "DK": "stripe",
    "FI": "stripe",
    "CH": "stripe",
    "AT": "stripe",
    "NZ": "stripe",
    "SG": "stripe",
    "AE": "stripe",
    # Morocco / MENA: leave as stripe if keys exist; otherwise upgrade is queued
    # until a local payment wall is configured.
    "MA": "stripe",
}


def _stripe_configured() -> bool:
    key = (getattr(settings, "STRIPE_SECRET_KEY", None) or "").strip()
    return bool(key) and not key.lower().startswith("your-")


def resolve_payment_provider(restaurant: Restaurant) -> PaymentProviderChoice:
    """Pick a billing provider for this tenant (by registered country)."""
    country = (getattr(restaurant, "country_code", None) or "").strip().upper()
    mapping = getattr(settings, "PAYMENT_PROVIDER_BY_COUNTRY", None) or _DEFAULT_COUNTRY_PROVIDERS
    default = getattr(settings, "DEFAULT_PAYMENT_PROVIDER", "stripe")
    provider: ProviderId = mapping.get(country, default)  # type: ignore[assignment]

    if provider == "stripe":
        if _stripe_configured():
            return PaymentProviderChoice(
                provider="stripe",
                configured=True,
                reason="Stripe is available for this tenant's country",
            )
        return PaymentProviderChoice(
            provider="stripe",
            configured=False,
            reason="Stripe is selected for this country but STRIPE_SECRET_KEY is not set",
        )

    return PaymentProviderChoice(
        provider="none",
        configured=False,
        reason=(
            f"No payment wall configured yet for country {country or 'unknown'}. "
            "Upgrade request can be queued until a provider is wired."
        ),
    )
