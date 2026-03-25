"""
EatNow (eat-now.io) webhook signature verification.

Docs: webhook payloads are POST JSON; verify HMAC-SHA256 over the *raw* request body
using the signing secret from EatNow → Settings → Integrations → Webhooks.

Header: X-EatNow-Signature — compare to f\"sha256={hmac_hex}\"
See: https://docs.eat-now.io/ (Webhooks / Signature verification)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def verify_eatnow_signature(raw_body: bytes, signature_header: Optional[str], secret: str) -> bool:
    if not secret or not signature_header:
        return False
    sig = signature_header.strip()
    expected_hex = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    expected = f"sha256={expected_hex}"
    if not sig:
        return False
    # EatNow sends e.g. "sha256=abc..." — timing-safe compare full value
    try:
        return hmac.compare_digest(expected.lower(), sig.lower())
    except Exception:
        return False


def parse_webhook_json(raw_body: bytes) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return (payload_dict, error_message)."""
    if not raw_body:
        return None, "empty body"
    try:
        data = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        logger.warning("eatnow_webhook: invalid json: %s", e)
        return None, "invalid json"
    if not isinstance(data, dict):
        return None, "payload must be an object"
    return data, None
