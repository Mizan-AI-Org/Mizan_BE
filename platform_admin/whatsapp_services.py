"""Platform WhatsApp config persistence and Meta Graph API helpers."""

from __future__ import annotations

import logging
from typing import Any

import requests
from django.conf import settings
from django.utils import timezone

from core.crypto import encrypt_text
from core.whatsapp_config import (
    clean_whatsapp_env_value,
    enrich_whatsapp_probe_failure,
    get_whatsapp_access_token,
    get_whatsapp_phone_number_id,
    get_whatsapp_verify_token,
    is_whatsapp_platform_auth_error,
    parse_whatsapp_api_error,
    probe_whatsapp_credentials,
    resolve_whatsapp_access_token,
)

from .models import PlatformWhatsAppConfig, WhatsAppMessageTemplate

logger = logging.getLogger(__name__)


def get_singleton_config() -> PlatformWhatsAppConfig | None:
    try:
        return PlatformWhatsAppConfig.objects.filter(pk=PlatformWhatsAppConfig.SINGLETON_ID).first()
    except Exception:
        return None


def get_or_create_singleton_config() -> PlatformWhatsAppConfig:
    obj, _ = PlatformWhatsAppConfig.objects.get_or_create(
        pk=PlatformWhatsAppConfig.SINGLETON_ID,
        defaults={"activation_phone": "212784476751"},
    )
    return obj


def _store_encrypted_token(plaintext: str) -> str:
    token = (plaintext or "").strip()
    if not token:
        return ""
    return f"enc:{encrypt_text(token)}"


def _resolve_stored_token(stored: str) -> str:
    return resolve_whatsapp_access_token(stored or "")


def effective_whatsapp_values() -> dict[str, str | bool]:
    """Merged DB + env values used at runtime."""
    row = get_singleton_config()
    env_token = clean_whatsapp_env_value(getattr(settings, "WHATSAPP_ACCESS_TOKEN", ""))
    env_phone = clean_whatsapp_env_value(getattr(settings, "WHATSAPP_PHONE_NUMBER_ID", ""))
    env_waba = clean_whatsapp_env_value(getattr(settings, "WHATSAPP_BUSINESS_ACCOUNT_ID", ""))
    env_verify = clean_whatsapp_env_value(getattr(settings, "WHATSAPP_VERIFY_TOKEN", ""))
    env_activation = clean_whatsapp_env_value(getattr(settings, "WHATSAPP_ACTIVATION_WA_PHONE", ""))

    if row and row.disconnected_at:
        env_token_resolved = resolve_whatsapp_access_token(env_token)
        has_env_creds = bool(env_token_resolved and env_phone)
        return {
            "phone_number_id": env_phone if has_env_creds else "",
            "business_account_id": env_waba if has_env_creds else "",
            "access_token": env_token_resolved if has_env_creds else "",
            "verify_token": env_verify if has_env_creds else "",
            "activation_phone": clean_whatsapp_env_value(row.activation_phone) or env_activation or "212784476751",
            "api_version": row.api_version or getattr(settings, "WHATSAPP_API_VERSION", "v22.0"),
            "miya_whatsapp_enabled": bool(
                getattr(settings, "MIYA_WHATSAPP_ENABLED", True) and has_env_creds
            ),
            "miya_voice_default": bool(getattr(settings, "MIYA_WHATSAPP_VOICE_DEFAULT", False)),
            "source": "env_fallback" if has_env_creds else "disconnected",
        }

    if not row:
        return {
            "phone_number_id": env_phone,
            "business_account_id": env_waba,
            "access_token": resolve_whatsapp_access_token(env_token),
            "verify_token": env_verify,
            "activation_phone": env_activation or "212784476751",
            "api_version": getattr(settings, "WHATSAPP_API_VERSION", "v22.0"),
            "miya_whatsapp_enabled": bool(getattr(settings, "MIYA_WHATSAPP_ENABLED", True)),
            "miya_voice_default": bool(getattr(settings, "MIYA_WHATSAPP_VOICE_DEFAULT", False)),
            "source": "env",
        }

    db_token = _resolve_stored_token(row.access_token_encrypted)
    token = db_token or resolve_whatsapp_access_token(env_token)
    phone = clean_whatsapp_env_value(row.phone_number_id) or env_phone
    waba = clean_whatsapp_env_value(row.business_account_id) or env_waba
    verify = clean_whatsapp_env_value(row.verify_token) or env_verify
    activation = clean_whatsapp_env_value(row.activation_phone) or env_activation or "212784476751"

    return {
        "phone_number_id": phone,
        "business_account_id": waba,
        "access_token": token,
        "verify_token": verify,
        "activation_phone": activation,
        "api_version": row.api_version or getattr(settings, "WHATSAPP_API_VERSION", "v22.0"),
        "miya_whatsapp_enabled": row.miya_whatsapp_enabled,
        "miya_voice_default": row.miya_voice_default,
        "source": "database" if (row.phone_number_id or row.access_token_encrypted) else "env",
    }


def config_has_access_token() -> bool:
    row = get_singleton_config()
    if row and row.disconnected_at:
        return bool(get_whatsapp_access_token())
    if row and (row.access_token_encrypted or "").strip():
        return True
    return bool(get_whatsapp_access_token())


def mask_token(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 8:
        return "••••••••"
    return f"{token[:4]}••••{token[-4:]}"


def serialize_config_for_api(request=None) -> dict[str, Any]:
    row = get_or_create_singleton_config()
    effective = effective_whatsapp_values()
    token_set = config_has_access_token()

    webhook_url = ""
    if request is not None:
        webhook_url = request.build_absolute_uri("/api/notifications/whatsapp/webhook/")
    else:
        base = (getattr(settings, "PUBLIC_API_BASE_URL", None) or "").rstrip("/")
        if base:
            webhook_url = f"{base}/api/notifications/whatsapp/webhook/"

    connected = (
        not row.disconnected_at
        and bool(row.last_probe_ok)
        and token_set
        and bool(effective.get("phone_number_id"))
    )

    if row.disconnected_at:
        return {
            "phone_number_id": "",
            "business_account_id": "",
            "verify_token": "",
            "activation_phone": row.activation_phone or "212784476751",
            "api_version": row.api_version or "v22.0",
            "miya_whatsapp_enabled": False,
            "miya_voice_default": False,
            "access_token_set": False,
            "access_token_masked": "",
            "webhook_callback_url": webhook_url,
            "connected": False,
            "disconnected": True,
            "disconnected_at": row.disconnected_at.isoformat(),
            "last_probe_at": None,
            "last_probe_ok": None,
            "last_probe_message": "",
            "display_phone_number": "",
            "verified_name": "",
            "config_source": "disconnected",
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    return {
        "phone_number_id": row.phone_number_id or effective.get("phone_number_id") or "",
        "business_account_id": row.business_account_id or effective.get("business_account_id") or "",
        "verify_token": row.verify_token or effective.get("verify_token") or "",
        "activation_phone": row.activation_phone or effective.get("activation_phone") or "",
        "api_version": row.api_version or effective.get("api_version") or "v22.0",
        "miya_whatsapp_enabled": row.miya_whatsapp_enabled,
        "miya_voice_default": row.miya_voice_default,
        "access_token_set": token_set,
        "access_token_masked": mask_token(effective.get("access_token") or ""),
        "webhook_callback_url": webhook_url,
        "connected": connected,
        "last_probe_at": row.last_probe_at.isoformat() if row.last_probe_at else None,
        "last_probe_ok": row.last_probe_ok,
        "last_probe_message": row.last_probe_message or "",
        "display_phone_number": row.display_phone_number or "",
        "verified_name": row.verified_name or "",
        "config_source": effective.get("source"),
        "disconnected": False,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def disconnect_config(user) -> PlatformWhatsAppConfig:
    """Clear platform-managed WhatsApp credentials and disable Miya on WhatsApp."""
    row = get_or_create_singleton_config()
    row.phone_number_id = ""
    row.business_account_id = ""
    row.access_token_encrypted = ""
    row.verify_token = ""
    row.display_phone_number = ""
    row.verified_name = ""
    row.last_probe_at = None
    row.last_probe_ok = None
    row.last_probe_message = ""
    row.miya_whatsapp_enabled = False
    row.miya_voice_default = False
    row.disconnected_at = timezone.now()
    row.updated_by = user
    row.save()
    WhatsAppMessageTemplate.objects.all().delete()
    logger.info("Platform WhatsApp disconnected by %s", getattr(user, "email", user))
    return row


def save_config(payload: dict[str, Any], user) -> PlatformWhatsAppConfig:
    row = get_or_create_singleton_config()

    for field in (
        "phone_number_id",
        "business_account_id",
        "verify_token",
        "activation_phone",
        "api_version",
    ):
        if field in payload:
            setattr(row, field, clean_whatsapp_env_value(str(payload.get(field) or "")))

    if "miya_whatsapp_enabled" in payload:
        row.miya_whatsapp_enabled = bool(payload["miya_whatsapp_enabled"])
    if "miya_voice_default" in payload:
        row.miya_voice_default = bool(payload["miya_voice_default"])

    raw_token = payload.get("access_token")
    if raw_token is not None:
        token = str(raw_token).strip()
        if token:
            row.access_token_encrypted = _store_encrypted_token(token)

    reconnecting = bool(clean_whatsapp_env_value(row.phone_number_id)) or bool(
        (row.access_token_encrypted or "").strip()
    )
    if reconnecting:
        row.disconnected_at = None

    row.updated_by = user
    row.save()
    return row


def run_connection_test(
    update_row: bool = True,
    *,
    phone_number_id: str | None = None,
    business_account_id: str | None = None,
    access_token: str | None = None,
    api_version: str | None = None,
    activation_phone: str | None = None,
) -> dict[str, Any]:
    resolved_token = (
        resolve_whatsapp_access_token(access_token)
        if access_token is not None
        else get_whatsapp_access_token()
    )
    resolved_phone = (
        clean_whatsapp_env_value(phone_number_id)
        if phone_number_id is not None
        else get_whatsapp_phone_number_id()
    )
    result = probe_whatsapp_credentials(
        phone_number_id=phone_number_id,
        access_token=access_token,
        api_version=api_version,
    )
    if not result.get("ok"):
        enriched = enrich_whatsapp_probe_failure(
            result,
            access_token=resolved_token,
            api_version=api_version,
            configured_phone_number_id=resolved_phone,
            configured_waba_id=business_account_id,
            activation_phone=activation_phone,
        )
        suggested_phone = enriched.get("suggested_phone_number_id")
        if suggested_phone and suggested_phone != resolved_phone:
            retry = probe_whatsapp_credentials(
                phone_number_id=str(suggested_phone),
                access_token=access_token,
                api_version=api_version,
            )
            if retry.get("ok"):
                result = {
                    **retry,
                    "auto_corrected": True,
                    "previous_phone_number_id": resolved_phone,
                    "suggested_business_account_id": enriched.get("suggested_business_account_id"),
                    "available_phone_numbers": enriched.get("available_phone_numbers") or [],
                    "fix_steps": enriched.get("fix_steps") or [],
                    "message": (
                        f"Connected using Phone Number ID {suggested_phone} "
                        f"({retry.get('display_phone_number') or retry.get('verified_name') or 'verified'}). "
                        "Save configuration to persist the corrected ID."
                    ),
                }
            else:
                result = enriched
        else:
            result = enriched
    if update_row:
        row = get_or_create_singleton_config()
        row.last_probe_at = timezone.now()
        row.last_probe_ok = bool(result.get("ok"))
        row.last_probe_message = (result.get("message") or "")[:500]
        if result.get("ok"):
            row.display_phone_number = result.get("display_phone_number") or ""
            row.verified_name = result.get("verified_name") or ""
            if result.get("auto_corrected"):
                row.phone_number_id = str(result.get("phone_number_id") or row.phone_number_id or "")
                suggested_waba = result.get("suggested_business_account_id")
                if suggested_waba:
                    row.business_account_id = str(suggested_waba)
        row.save(update_fields=[
            "last_probe_at",
            "last_probe_ok",
            "last_probe_message",
            "display_phone_number",
            "verified_name",
            "phone_number_id",
            "business_account_id",
        ])
    if result.get("ok"):
        try:
            sub = ensure_django_whatsapp_webhook_subscription()
            result["webhook_subscription"] = sub
        except Exception as exc:
            logger.warning("WhatsApp webhook subscription ensure failed: %s", exc)
            result["webhook_subscription"] = {"ok": False, "error": str(exc)[:200]}
    return result


def ensure_django_whatsapp_webhook_subscription() -> dict[str, Any]:
    """
    Point Meta WABA override callback at Django's WhatsApp webhook.

    Mastra's channel webhook is not used for production inbound — without this,
    messages can show as delivered in WhatsApp while Miya never receives them.
    """
    waba = _waba_id()
    token = get_whatsapp_access_token()
    verify = get_whatsapp_verify_token()
    base = (getattr(settings, "PUBLIC_API_BASE_URL", None) or "https://api.heymizan.ai").rstrip("/")
    callback = f"{base}/api/notifications/whatsapp/webhook/"
    if not waba or not token or not verify:
        return {
            "ok": False,
            "error": "missing_waba_token_or_verify",
            "callback_uri": callback,
        }

    version = _api_version()
    url = f"https://graph.facebook.com/{version}/{waba}/subscribed_apps"
    try:
        resp = requests.post(
            url,
            headers=_graph_headers(token),
            data={
                "override_callback_uri": callback,
                "verify_token": verify,
            },
            timeout=20,
        )
        data = resp.json() if resp.content else {}
        ok = resp.status_code < 400 and bool(data.get("success", True))
        if not ok:
            logger.warning(
                "WABA subscribed_apps override failed status=%s body=%s",
                resp.status_code,
                data,
            )
        else:
            logger.info("WABA webhook override set to %s", callback)
        return {
            "ok": ok,
            "callback_uri": callback,
            "status_code": resp.status_code,
            "response": data,
        }
    except Exception as exc:
        logger.exception("WABA subscribed_apps override error")
        return {"ok": False, "error": str(exc)[:200], "callback_uri": callback}


def _graph_headers(token: str | None = None) -> dict[str, str]:
    bearer = token or get_whatsapp_access_token()
    return {"Authorization": f"Bearer {bearer}"}


def _waba_id() -> str:
    effective = effective_whatsapp_values()
    waba = clean_whatsapp_env_value(str(effective.get("business_account_id") or ""))
    if waba:
        return waba
    row = get_singleton_config()
    if row and row.business_account_id:
        return clean_whatsapp_env_value(row.business_account_id)
    return clean_whatsapp_env_value(getattr(settings, "WHATSAPP_BUSINESS_ACCOUNT_ID", ""))


def _api_version() -> str:
    effective = effective_whatsapp_values()
    return str(effective.get("api_version") or getattr(settings, "WHATSAPP_API_VERSION", "v22.0"))


def _extract_template_fields(components: list) -> tuple[str, str, str]:
    body = footer = header = ""
    for comp in components or []:
        if not isinstance(comp, dict):
            continue
        ctype = (comp.get("type") or "").upper()
        if ctype == "BODY":
            body = comp.get("text") or ""
        elif ctype == "FOOTER":
            footer = comp.get("text") or ""
        elif ctype == "HEADER":
            header = comp.get("text") or ""
    return body, footer, header


def sync_templates_from_meta() -> dict[str, Any]:
    token = get_whatsapp_access_token()
    waba = _waba_id()
    if not token:
        return {"ok": False, "error": "WhatsApp access token is not configured"}
    if not waba:
        return {"ok": False, "error": "WhatsApp Business Account ID is not configured"}

    api_version = _api_version()
    url = f"https://graph.facebook.com/{api_version}/{waba}/message_templates"
    params = {"limit": 100, "fields": "id,name,language,status,category,components"}
    synced = 0
    errors: list[str] = []

    while url:
        try:
            resp = requests.get(url, headers=_graph_headers(token), params=params, timeout=20)
            data = resp.json()
        except requests.RequestException as exc:
            return {"ok": False, "error": str(exc)[:240]}

        if resp.status_code != 200:
            message = parse_whatsapp_api_error(data)
            return {"ok": False, "error": message or resp.text[:240]}

        for item in data.get("data") or []:
            if not isinstance(item, dict):
                continue
            meta_id = str(item.get("id") or "")
            if not meta_id:
                continue
            components = item.get("components") or []
            body, footer, header = _extract_template_fields(components)
            WhatsAppMessageTemplate.objects.update_or_create(
                meta_id=meta_id,
                defaults={
                    "name": item.get("name") or "",
                    "language": item.get("language") or "en_US",
                    "category": (item.get("category") or "").upper(),
                    "status": (item.get("status") or "").upper(),
                    "body_text": body,
                    "footer_text": footer,
                    "header_text": header,
                    "components_json": components,
                },
            )
            synced += 1

        paging = data.get("paging") or {}
        next_url = (paging.get("next") or "").strip()
        url = next_url
        params = None  # next URL includes query string

    return {"ok": True, "synced": synced, "errors": errors}


def create_meta_template(payload: dict[str, Any]) -> dict[str, Any]:
    token = get_whatsapp_access_token()
    waba = _waba_id()
    if not token or not waba:
        return {"ok": False, "error": "Configure access token and WABA ID first"}

    name = (payload.get("name") or "").strip().lower().replace(" ", "_")
    language = (payload.get("language") or "en_US").strip()
    category = (payload.get("category") or "UTILITY").strip().upper()
    body = (payload.get("body_text") or "").strip()
    footer = (payload.get("footer_text") or "").strip()

    if not name or not body:
        return {"ok": False, "error": "Template name and body are required"}

    components: list[dict[str, Any]] = [{"type": "BODY", "text": body}]
    if footer:
        components.append({"type": "FOOTER", "text": footer})

    api_version = _api_version()
    url = f"https://graph.facebook.com/{api_version}/{waba}/message_templates"
    body_payload = {
        "name": name,
        "language": language,
        "category": category,
        "components": components,
    }

    try:
        resp = requests.post(url, headers={**_graph_headers(token), "Content-Type": "application/json"}, json=body_payload, timeout=25)
        data = resp.json()
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc)[:240]}

    if resp.status_code not in (200, 201):
        return {"ok": False, "error": parse_whatsapp_api_error(data) or resp.text[:240]}

    sync_templates_from_meta()
    return {"ok": True, "meta": data}


def delete_meta_template(template: WhatsAppMessageTemplate) -> dict[str, Any]:
    token = get_whatsapp_access_token()
    waba = _waba_id()
    if not token or not waba:
        return {"ok": False, "error": "Configure access token and WABA ID first"}

    api_version = _api_version()
    url = f"https://graph.facebook.com/{api_version}/{waba}/message_templates"
    params = {"hsm_id": template.meta_id, "name": template.name}

    try:
        resp = requests.delete(url, headers=_graph_headers(token), params=params, timeout=20)
        data = resp.json() if resp.content else {}
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc)[:240]}

    if resp.status_code not in (200, 204):
        message = parse_whatsapp_api_error(data)
        if not is_whatsapp_platform_auth_error(message):
            return {"ok": False, "error": message or resp.text[:240]}

    template.delete()
    return {"ok": True}


def list_templates() -> list[dict[str, Any]]:
    return [
        {
            "id": str(t.pk),
            "meta_id": t.meta_id,
            "name": t.name,
            "language": t.language,
            "category": t.category,
            "status": t.status,
            "body_text": t.body_text,
            "footer_text": t.footer_text,
            "header_text": t.header_text,
            "synced_at": t.synced_at.isoformat() if t.synced_at else None,
        }
        for t in WhatsAppMessageTemplate.objects.all()
    ]
