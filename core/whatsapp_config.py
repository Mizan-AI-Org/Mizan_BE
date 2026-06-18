"""WhatsApp Cloud API credential helpers and user-safe error mapping."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_PLATFORM_AUTH_HINTS = (
    "access token could not be decrypted",
    "invalid oauth access token",
    "error validating access token",
    "session has expired",
    "expired token",
    "invalid token",
    "(#190)",
    "(#102)",
    "(#10)",
    "oauth",
    "permissions error",
    "missing permissions",
    "not authorized",
    "unauthorized",
)


def clean_whatsapp_env_value(raw: str | None) -> str:
    """Normalize secrets copied from Meta / hosting dashboards."""
    if not raw:
        return ""
    value = str(raw).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1].strip()
    # Meta user tokens usually start with EAA — strip accidental whitespace
    # from multiline secret managers without touching other formats.
    if value.startswith("EAA"):
        value = re.sub(r"\s+", "", value)
    return value


def resolve_whatsapp_access_token(raw: str | None = None) -> str:
    """Return the WhatsApp bearer token, optionally decrypting ``enc:`` values."""
    token = clean_whatsapp_env_value(
        raw if raw is not None else getattr(settings, "WHATSAPP_ACCESS_TOKEN", "")
    )
    if token.startswith("enc:"):
        from core.crypto import decrypt_text

        try:
            token = decrypt_text(token[4:])
        except Exception as exc:
            logger.error("WHATSAPP_ACCESS_TOKEN Fernet decrypt failed: %s", exc)
            return ""
    return token


def get_whatsapp_access_token() -> str:
    return resolve_whatsapp_access_token()


def get_whatsapp_phone_number_id() -> str:
    return clean_whatsapp_env_value(getattr(settings, "WHATSAPP_PHONE_NUMBER_ID", ""))


def parse_whatsapp_api_error(payload: Any) -> str:
    """Extract a human-readable message from a Meta Graph API error payload."""
    if payload is None:
        return ""
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return ""
        if text.startswith("{"):
            try:
                return parse_whatsapp_api_error(json.loads(text))
            except Exception:
                return text[:500]
        return text[:500]
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            msg = (err.get("message") or "").strip()
            code = err.get("code")
            if msg and code:
                return f"{msg} (#{code})"
            return msg or str(payload)[:500]
        return str(payload)[:500]
    return str(payload)[:500]


def is_whatsapp_platform_auth_error(message: str | None) -> bool:
    if not message:
        return False
    lower = str(message).strip().lower()
    return any(hint in lower for hint in _PLATFORM_AUTH_HINTS)


def user_facing_whatsapp_error(message: str | None) -> str:
    """Rewrite provider/auth errors into manager-safe language."""
    if not message:
        return ""
    if is_whatsapp_platform_auth_error(message):
        return (
            "WhatsApp is temporarily unavailable on our side. "
            "Your message was saved in-app — our team is fixing the connection."
        )
    lower = str(message).lower()
    if "phone" in lower and (
        "invalid" in lower or "not a whatsapp" in lower or "not registered" in lower
    ):
        return (
            "That phone number does not appear to be on WhatsApp. "
            "Check the staff profile and include the country code."
        )
    if "rate" in lower and "limit" in lower:
        return "WhatsApp is rate-limiting us — try again in a few minutes."
    return str(message).strip()[:240]


def probe_whatsapp_credentials() -> dict[str, Any]:
    """Lightweight live check against Meta Graph API (phone-number profile)."""
    token = get_whatsapp_access_token()
    phone_id = get_whatsapp_phone_number_id()
    if not token:
        return {
            "ok": False,
            "reason": "missing_token",
            "message": "WHATSAPP_ACCESS_TOKEN is not set",
        }
    if not phone_id:
        return {
            "ok": False,
            "reason": "missing_phone_id",
            "message": "WHATSAPP_PHONE_NUMBER_ID is not set",
        }

    api_version = getattr(settings, "WHATSAPP_API_VERSION", "v22.0")
    url = f"https://graph.facebook.com/{api_version}/{phone_id}"
    try:
        resp = requests.get(
            url,
            params={"fields": "display_phone_number,verified_name"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=8,
        )
        try:
            data = resp.json()
        except Exception:
            data = {"error": {"message": resp.text[:240]}}

        if resp.status_code == 200 and isinstance(data, dict) and "id" in data:
            return {
                "ok": True,
                "phone_number_id": phone_id,
                "display_phone_number": data.get("display_phone_number"),
                "verified_name": data.get("verified_name"),
            }

        message = parse_whatsapp_api_error(data)
        return {
            "ok": False,
            "reason": "auth_error" if is_whatsapp_platform_auth_error(message) else "api_error",
            "status_code": resp.status_code,
            "message": message or resp.text[:240],
            "token_length": len(token),
        }
    except requests.RequestException as exc:
        logger.warning("WhatsApp credential probe failed: %s", exc)
        return {
            "ok": False,
            "reason": "network_error",
            "message": str(exc)[:240],
        }
