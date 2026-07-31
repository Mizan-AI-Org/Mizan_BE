"""Resolve WhatsApp sender → Mizan user (shared by Django webhook and Mastra channel)."""

from __future__ import annotations

from typing import Any

from accounts.models import CustomUser
from accounts.services import _find_active_user_by_phone, normalize_activation_phone_inbound
from notifications.models import WhatsAppSession


def normalize_whatsapp_phone(from_phone: str | None) -> str:
    phone_digits = "".join(filter(str.isdigit, str(from_phone or "")))
    return normalize_activation_phone_inbound(phone_digits) or phone_digits


def resolve_whatsapp_user(phone_digits: str) -> tuple[CustomUser | None, WhatsAppSession | None]:
    """Match webhook user resolution: session → phone lookup → suffix fallback."""
    phone_digits = normalize_whatsapp_phone(phone_digits)
    if not phone_digits:
        return None, None

    session = WhatsAppSession.objects.filter(phone=phone_digits).first()
    user = session.user if (session and session.user_id) else None
    if not user:
        user = _find_active_user_by_phone(phone_digits)
    if not user:
        qs = CustomUser.objects.filter(phone__isnull=False).filter(phone__regex=r"\d")
        if session and session.user_id and getattr(session.user, "restaurant_id", None):
            qs = qs.filter(restaurant_id=session.user.restaurant_id)
        user = qs.filter(phone__icontains=phone_digits[-9:]).first()

    if not session:
        session = WhatsAppSession.objects.create(phone=phone_digits, user=user)
    elif user and not session.user_id:
        session.user = user
        session.save(update_fields=["user"])
    elif user and session.user is None:
        session.user = user
        session.save(update_fields=["user"])

    return user, session


def whatsapp_session_hint(session: WhatsAppSession | None, phone_digits: str) -> dict[str, Any]:
    hint: dict[str, Any] = {
        "thread_id": f"wa-{getattr(session, 'id', phone_digits)}",
        "whatsapp_session_id": f"wa-{getattr(session, 'id', phone_digits)}",
    }
    if session and session.user_id and getattr(session.user, "restaurant_id", None):
        hint["restaurant_id"] = str(session.user.restaurant_id)
    return hint
