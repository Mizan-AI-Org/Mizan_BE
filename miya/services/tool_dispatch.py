"""In-process dispatch for Miya agent tools (avoids HTTP self-deadlock)."""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlsplit

from django.urls import resolve
from rest_framework.test import APIRequestFactory

logger = logging.getLogger(__name__)

_factory = APIRequestFactory()


def _internal_bases() -> set[str]:
    return {
        "",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:8001",
        "http://localhost:8001",
    }


def should_dispatch_in_process(api_base: str) -> bool:
    """Miya runs inside Django — in-process dispatch avoids single-worker deadlocks."""
    if api_base is None:
        return True
    base = (api_base or "").rstrip("/")
    if base in _internal_bases():
        return True
    # External base URL still deadlocks on one daphne worker; prefer in-process when routes exist locally.
    return True


def dispatch_agent_request(
    method: str,
    path: str,
    *,
    json_payload: dict[str, Any] | None,
    headers: dict[str, str] | None,
) -> tuple[int, Any]:
    """Call a Mizan agent route in-process. ``path`` must start with /api/."""
    payload = dict(json_payload or {})
    hdrs = dict(headers or {})
    method_upper = (method or "POST").upper()

    if method_upper == "GET":
        flat: dict[str, Any] = {}
        for key, val in payload.items():
            if isinstance(val, (list, tuple)):
                flat[key] = val[0] if val else ""
            elif val is not None:
                flat[key] = val
        request = _factory.get(path, flat, format="json")
    else:
        request = _factory.post(path, payload, format="json")

    for name, value in hdrs.items():
        if not value:
            continue
        if name.lower() == "authorization":
            request.META["HTTP_AUTHORIZATION"] = value
        elif name.lower() == "content-type":
            request.META["CONTENT_TYPE"] = value
        else:
            meta_key = "HTTP_" + name.upper().replace("-", "_")
            request.META[meta_key] = value

    match = resolve(path)
    try:
        response = match.func(request, *match.args, **match.kwargs)
    except Exception as exc:
        logger.exception("In-process agent dispatch failed for %s %s", method_upper, path)
        return 500, {"success": False, "error": str(exc)[:200]}

    if hasattr(response, "data"):
        body = response.data
    else:
        try:
            raw = response.content.decode() if response.content else ""
            body = json.loads(raw) if raw else {}
        except Exception:
            body = {"raw": str(response.content)[:500]}

    status_code = getattr(response, "status_code", 500)
    return int(status_code), body


def split_internal_tool_url(url: str) -> tuple[str, str]:
    """Return (method_path, '') from a full or relative agent URL."""
    if url.startswith("/api/"):
        return url, ""
    parts = urlsplit(url)
    return parts.path or url, parts.query
