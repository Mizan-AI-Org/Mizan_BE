"""ISO country code helpers for Restaurant tenants."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from accounts.models import Restaurant

MOROCCO_TIMEZONES = frozenset({"Africa/Casablanca"})


def _phone_digits(phone: str | None) -> str:
    if not phone:
        return ""
    return re.sub(r"\D", "", str(phone))


def restaurant_looks_moroccan(
    *,
    timezone: str | None = None,
    currency: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    language: str | None = None,
) -> bool:
    tz = (timezone or "").strip()
    if tz in MOROCCO_TIMEZONES or "Casablanca" in tz:
        return True
    if (currency or "").strip().upper() == "MAD":
        return True
    digits = _phone_digits(phone)
    if digits.startswith("212"):
        return True
    em = (email or "").strip().lower()
    if em.endswith(".ma"):
        return True
    lang = (language or "").strip().lower()
    if lang in ("ma", "ar") and tz in MOROCCO_TIMEZONES:
        return True
    return False


def normalize_country_code(
    code: str | None,
    *,
    timezone: str | None = None,
    currency: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    language: str | None = None,
    default: str = "MA",
) -> str:
    """Return a canonical ISO 3166-1 alpha-2 country code for a tenant."""
    stripped = (code or "").strip()
    raw = stripped.upper()

    # Darija language code "ma" was sometimes stored in country_code by mistake.
    if stripped.lower() == "ma":
        return "MA"

    if raw == "MA":
        return "MA"

    # Malaysia vs Morocco: MY is wrong for Moroccan tenants.
    if raw == "MY":
        if restaurant_looks_moroccan(
            timezone=timezone,
            currency=currency,
            phone=phone,
            email=email,
            language=language,
        ):
            return "MA"
        return "MY"

    if raw:
        return raw[:5]

    if restaurant_looks_moroccan(
        timezone=timezone,
        currency=currency,
        phone=phone,
        email=email,
        language=language,
    ):
        return default

    return default


def normalize_country_code_for_restaurant(restaurant: Restaurant) -> str:
    return normalize_country_code(
        getattr(restaurant, "country_code", None),
        timezone=getattr(restaurant, "timezone", None),
        currency=getattr(restaurant, "currency", None),
        phone=getattr(restaurant, "phone", None),
        email=getattr(restaurant, "email", None),
        language=getattr(restaurant, "language", None),
    )
