"""
Lightweight backend i18n helpers for operational messaging.

Goals:
- Use staff preferred language when available; otherwise fall back to restaurant.language; else English.
- Never mix languages within one message (format all dynamic tokens consistently).
- Graceful fallback to English if a translation key is missing.
- Log missing translation keys for completion later.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = ("en", "fr", "ar")


def normalize_language(value: str | None) -> str:
    if not value:
        return "en"
    v = str(value).strip().lower()
    # Back-compat: older builds stored "ma" (Darija); treat as Arabic system language.
    if v in ("ma", "ar-ma"):
        return "ar"
    if v in SUPPORTED_LANGUAGES:
        return v
    return "en"


def get_effective_language(*, user=None, restaurant=None, fallback: str = "en") -> str:
    """
    Resolve effective language for a message recipient.
    Priority:
    1) user.preferred_language (if set)
    2) restaurant.language (if set)
    3) fallback (default: en)
    """
    try:
        pref = getattr(user, "preferred_language", None)
        if pref:
            return normalize_language(pref)
    except Exception:
        pass

    try:
        rest = restaurant or getattr(user, "restaurant", None)
        if rest is not None:
            return normalize_language(getattr(rest, "language", None))
    except Exception:
        pass

    return normalize_language(fallback)


def whatsapp_language_code(lang: str) -> str:
    """
    WhatsApp Cloud API language codes.
    Keep conservative defaults to avoid template mismatch.
    """
    lang = normalize_language(lang)
    if lang == "fr":
        return "fr_FR"
    if lang == "ar":
        return "ar"
    return "en_US"


# -------------------------------------------------------------------------
# Simple message catalog for operational notifications/templates
# -------------------------------------------------------------------------

_CATALOG: dict[str, dict[str, str]] = {
    "en": {
        "time.minutes_from_now": "{n} minutes",
        "time.in_minutes": "in {n} minutes",
        "shift.role.fallback": "STAFF",
        "notify.shift.assigned.title": "New Shift Assigned",
        "notify.shift.assigned.body": "You have been assigned a new shift on {start}.",
        "notify.shift.updated.title": "Shift Updated",
        "notify.shift.updated.body": "Your shift on {start} has been updated.",
        "notify.shift.cancelled.title": "Shift Cancelled",
        "notify.shift.cancelled.body": "Your shift on {start} has been cancelled.",
        "notify.shift.reminder.title": "Upcoming Shift Reminder",
        "notify.shift.reminder.body": "Reminder: You have a shift starting soon on {start}.",
        "checklist.reminder": "Hi {name}! You have {count} tasks assigned for your shift.\n\nPreview: {preview}\n\nGood luck!",
        "checklist.preview.more": "...",
    },
    "fr": {
        "time.minutes_from_now": "{n} minutes",
        "time.in_minutes": "dans {n} minutes",
        "shift.role.fallback": "PERSONNEL",
        "notify.shift.assigned.title": "Nouveau service attribué",
        "notify.shift.assigned.body": "Un nouveau service vous a été attribué le {start}.",
        "notify.shift.updated.title": "Service mis à jour",
        "notify.shift.updated.body": "Votre service du {start} a été mis à jour.",
        "notify.shift.cancelled.title": "Service annulé",
        "notify.shift.cancelled.body": "Votre service du {start} a été annulé.",
        "notify.shift.reminder.title": "Rappel de service",
        "notify.shift.reminder.body": "Rappel : vous avez un service qui commence bientôt le {start}.",
        "checklist.reminder": "Bonjour {name} ! Vous avez {count} tâches assignées pour votre service.\n\nAperçu : {preview}\n\nBon courage !",
        "checklist.preview.more": "...",
    },
    "ar": {
        "time.minutes_from_now": "{n} دقيقة",
        "time.in_minutes": "بعد {n} دقيقة",
        "shift.role.fallback": "طاقم",
        "notify.shift.assigned.title": "تم تعيين وردية جديدة",
        "notify.shift.assigned.body": "تم تعيين وردية جديدة لك بتاريخ {start}.",
        "notify.shift.updated.title": "تم تحديث الوردية",
        "notify.shift.updated.body": "تم تحديث ورديتك بتاريخ {start}.",
        "notify.shift.cancelled.title": "تم إلغاء الوردية",
        "notify.shift.cancelled.body": "تم إلغاء ورديتك بتاريخ {start}.",
        "notify.shift.reminder.title": "تذكير بوردية قادمة",
        "notify.shift.reminder.body": "تذكير: لديك وردية ستبدأ قريبًا بتاريخ {start}.",
        "checklist.reminder": "مرحبًا {name}! لديك {count} مهام مخصصة لورديتك.\n\nمعاينة: {preview}\n\nبالتوفيق!",
        "checklist.preview.more": "...",
    },
}


def tr(key: str, lang: str, **kwargs: Any) -> str:
    """
    Translate a key with safe English fallback and missing-key logging.
    """
    lang = normalize_language(lang)
    table = _CATALOG.get(lang) or _CATALOG["en"]
    text = table.get(key)
    if text is None:
        # log and fallback to english
        logger.warning("[i18n] Missing translation key '%s' for lang '%s'", key, lang)
        text = _CATALOG["en"].get(key, key)
    try:
        return text.format(**kwargs)
    except Exception:
        # If formatting fails, return raw text (still single-language)
        return text

