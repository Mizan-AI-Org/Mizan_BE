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


def _platform_whatsapp_effective() -> dict:
    """Lazy import — platform_admin may not be migrated yet at startup."""
    try:
        from platform_admin.whatsapp_services import effective_whatsapp_values

        return effective_whatsapp_values()
    except Exception:
        return {}


def get_whatsapp_access_token() -> str:
    effective = _platform_whatsapp_effective()
    token = effective.get("access_token")
    if token:
        return str(token)
    return resolve_whatsapp_access_token()


def get_whatsapp_phone_number_id() -> str:
    effective = _platform_whatsapp_effective()
    phone = effective.get("phone_number_id")
    if phone:
        return clean_whatsapp_env_value(str(phone))
    return clean_whatsapp_env_value(getattr(settings, "WHATSAPP_PHONE_NUMBER_ID", ""))


def get_whatsapp_business_account_id() -> str:
    effective = _platform_whatsapp_effective()
    waba = effective.get("business_account_id")
    if waba:
        return clean_whatsapp_env_value(str(waba))
    return clean_whatsapp_env_value(getattr(settings, "WHATSAPP_BUSINESS_ACCOUNT_ID", ""))


def get_whatsapp_verify_token() -> str:
    effective = _platform_whatsapp_effective()
    verify = effective.get("verify_token")
    if verify:
        return clean_whatsapp_env_value(str(verify))
    return clean_whatsapp_env_value(getattr(settings, "WHATSAPP_VERIFY_TOKEN", ""))


def get_whatsapp_activation_phone() -> str:
    effective = _platform_whatsapp_effective()
    phone = effective.get("activation_phone")
    if phone:
        return clean_whatsapp_env_value(str(phone))
    return clean_whatsapp_env_value(getattr(settings, "WHATSAPP_ACTIVATION_WA_PHONE", ""))


def get_whatsapp_api_version() -> str:
    effective = _platform_whatsapp_effective()
    version = effective.get("api_version")
    if version:
        return str(version)
    return getattr(settings, "WHATSAPP_API_VERSION", "v22.0")


def get_miya_whatsapp_enabled() -> bool:
    effective = _platform_whatsapp_effective()
    if effective:
        return bool(effective.get("miya_whatsapp_enabled", True))
    return bool(getattr(settings, "MIYA_WHATSAPP_ENABLED", True))


def get_miya_whatsapp_voice_default() -> bool:
    effective = _platform_whatsapp_effective()
    if effective:
        return bool(effective.get("miya_voice_default", False))
    return bool(getattr(settings, "MIYA_WHATSAPP_VOICE_DEFAULT", False))


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
    if "131047" in lower or "allowed window" in lower or "24 hour" in lower or "24-hour" in lower:
        return (
            "WhatsApp only allows free messages within 24h of the staff member's "
            "last reply. Ask them to message Miya once, then try again."
        )
    if "phone" in lower and (
        "invalid" in lower or "not a whatsapp" in lower or "not registered" in lower
    ):
        return (
            "That phone number does not appear to be on WhatsApp. "
            "Check the staff profile and include the country code."
        )
    if "rate" in lower and "limit" in lower:
        return "WhatsApp is rate-limiting us — try again in a few minutes."
    if "manager_message" in lower and "template" in lower:
        return (
            "Staff haven't messaged recently (WhatsApp 24h rule). "
            "Ask them to send any message to Miya, then try again — "
            "or set up the manager_message WhatsApp template."
        )
    return str(message).strip()[:240]


def _graph_get(
    path: str,
    token: str,
    api_version: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = 12,
) -> tuple[int, Any]:
    url = f"https://graph.facebook.com/{api_version}/{path.lstrip('/')}"
    try:
        resp = requests.get(
            url,
            params=params or {},
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        try:
            return resp.status_code, resp.json()
        except Exception:
            return resp.status_code, {"error": {"message": resp.text[:240]}}
    except requests.RequestException as exc:
        return 0, {"error": {"message": str(exc)[:240]}}


def _digits_only(value: str | None) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def fetch_assigned_whatsapp_phone_numbers(
    access_token: str,
    *,
    api_version: str | None = None,
) -> dict[str, Any]:
    """List phone numbers reachable by this token via assigned WhatsApp Business Accounts."""
    token = resolve_whatsapp_access_token(access_token)
    version = (api_version or get_whatsapp_api_version()).strip() or "v22.0"
    if not token:
        return {"ok": False, "message": "Access token is not set", "phone_numbers": []}

    status_code, me_data = _graph_get("me", token, version, params={"fields": "id,name"})
    if status_code != 200 or not isinstance(me_data, dict):
        return {
            "ok": False,
            "message": parse_whatsapp_api_error(me_data) or "Could not read Meta token identity",
            "phone_numbers": [],
        }

    user_id = str(me_data.get("id") or "")
    status_code, assigned_data = _graph_get(
        f"{user_id}/assigned_whatsapp_business_accounts",
        token,
        version,
        params={"fields": "id,name"},
    )
    if status_code != 200 or not isinstance(assigned_data, dict):
        return {
            "ok": False,
            "message": parse_whatsapp_api_error(assigned_data) or "Could not list assigned WhatsApp accounts",
            "phone_numbers": [],
            "token_identity": me_data,
        }

    waba_rows = assigned_data.get("data") or []
    phone_numbers: list[dict[str, str]] = []
    for waba in waba_rows:
        if not isinstance(waba, dict):
            continue
        waba_id = str(waba.get("id") or "")
        if not waba_id:
            continue
        status_code, phones_data = _graph_get(
            f"{waba_id}/phone_numbers",
            token,
            version,
            params={"fields": "id,display_phone_number,verified_name"},
        )
        if status_code != 200 or not isinstance(phones_data, dict):
            continue
        for phone in phones_data.get("data") or []:
            if not isinstance(phone, dict):
                continue
            phone_id = str(phone.get("id") or "")
            if not phone_id:
                continue
            phone_numbers.append(
                {
                    "phone_number_id": phone_id,
                    "display_phone_number": str(phone.get("display_phone_number") or ""),
                    "verified_name": str(phone.get("verified_name") or ""),
                    "business_account_id": waba_id,
                    "business_account_name": str(waba.get("name") or ""),
                }
            )

    return {
        "ok": True,
        "token_identity": {"id": user_id, "name": me_data.get("name") or ""},
        "assigned_waba_count": len(waba_rows),
        "phone_numbers": phone_numbers,
    }


def enrich_whatsapp_probe_failure(
    result: dict[str, Any],
    *,
    access_token: str,
    api_version: str | None = None,
    configured_phone_number_id: str | None = None,
    configured_waba_id: str | None = None,
    activation_phone: str | None = None,
) -> dict[str, Any]:
    """Turn Meta (#100) failures into actionable platform-admin guidance."""
    if result.get("ok"):
        return result

    token = resolve_whatsapp_access_token(access_token)
    assigned = fetch_assigned_whatsapp_phone_numbers(token, api_version=api_version)
    phone_numbers = assigned.get("phone_numbers") or []
    assigned_count = int(assigned.get("assigned_waba_count") or 0)

    result["assigned_waba_count"] = assigned_count
    result["available_phone_numbers"] = phone_numbers
    result["token_identity"] = assigned.get("token_identity") or {}

    fix_steps = [
        "Meta Business Settings → Users → System users → open the system user that owns this token.",
        "Add assets → WhatsApp accounts → select the Mizan central number (+212784476751).",
        "Grant Full control, save, then generate a new permanent token for the Mizan AI app.",
        "Paste the new token here, save, and run Test API Connection again.",
    ]
    result["fix_steps"] = fix_steps

    if assigned_count == 0:
        result["reason"] = "no_assigned_waba"
        result["message"] = (
            "Your Meta token is valid but this system user has no WhatsApp Business Account assigned. "
            "Assign the WhatsApp account to the system user in Meta Business Settings, regenerate the token, "
            "then test again."
        )
        return result

    configured_id = clean_whatsapp_env_value(configured_phone_number_id or "")
    known_ids = {p.get("phone_number_id") for p in phone_numbers}
    if configured_id and configured_id not in known_ids:
        readable = ", ".join(
            f"{p.get('display_phone_number') or 'number'} (ID {p.get('phone_number_id')})"
            for p in phone_numbers
        )
        result["reason"] = "wrong_phone_number_id"
        result["message"] = (
            f"Phone Number ID {configured_id} is not accessible with this token. "
            f"Use one of: {readable}."
        )

    activation_digits = _digits_only(activation_phone)
    if activation_digits:
        for phone in phone_numbers:
            display_digits = _digits_only(phone.get("display_phone_number"))
            if display_digits.endswith(activation_digits) or activation_digits.endswith(display_digits):
                result["suggested_phone_number_id"] = phone.get("phone_number_id")
                result["suggested_business_account_id"] = phone.get("business_account_id")
                break

    if phone_numbers and not result.get("suggested_phone_number_id"):
        first = phone_numbers[0]
        result["suggested_phone_number_id"] = first.get("phone_number_id")
        result["suggested_business_account_id"] = first.get("business_account_id")

    configured_waba = clean_whatsapp_env_value(configured_waba_id or "")
    known_wabas = {p.get("business_account_id") for p in phone_numbers}
    if configured_waba and configured_waba not in known_wabas and phone_numbers:
        result["suggested_business_account_id"] = phone_numbers[0].get("business_account_id")

    return result


def probe_whatsapp_credentials(
    *,
    phone_number_id: str | None = None,
    access_token: str | None = None,
    api_version: str | None = None,
) -> dict[str, Any]:
    """Lightweight live check against Meta Graph API (phone-number profile)."""
    token = (
        resolve_whatsapp_access_token(access_token)
        if access_token is not None
        else get_whatsapp_access_token()
    )
    phone_id = (
        clean_whatsapp_env_value(phone_number_id)
        if phone_number_id is not None
        else get_whatsapp_phone_number_id()
    )
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

    version = (api_version or get_whatsapp_api_version()).strip() or "v22.0"
    url = f"https://graph.facebook.com/{version}/{phone_id}"
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
