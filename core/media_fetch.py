"""Download remote media for agent endpoints (WhatsApp / Meta URLs)."""
from __future__ import annotations

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def fetch_remote_media_bytes(url: str, *, timeout: int = 30) -> tuple[bytes | None, str | None]:
    """Return (bytes, content_type) for a remote image/document URL.

    WhatsApp / Meta temporary URLs require the platform access token.
    """
    raw = (url or "").strip()
    if not raw:
        return None, None

    headers: dict[str, str] = {}
    token = getattr(settings, "WHATSAPP_ACCESS_TOKEN", "") or ""
    if token and (
        "graph.facebook.com" in raw
        or "lookaside.fbsbx.com" in raw
        or "fbcdn.net" in raw
    ):
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = requests.get(raw, headers=headers, timeout=timeout)
        resp.raise_for_status()
        content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
        if not resp.content:
            return None, content_type or None
        return resp.content, content_type or None
    except Exception:
        logger.exception("fetch_remote_media_bytes failed for url=%s", raw[:120])
        return None, None
