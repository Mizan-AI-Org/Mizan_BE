"""Normalize Miya replies for dashboard + WhatsApp (plain, friendly text)."""

from __future__ import annotations

import re
from typing import Any

_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_BULLET_RE = re.compile(r"^\s*[-•]\s+", re.MULTILINE)
_NUMBERED_BULLET_RE = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
# Presigned S3 / long signed URLs — never show raw to users.
_PRESIGNED_URL_RE = re.compile(
    r"https?://[^\s\])\"']+(?:X-Amz-[A-Za-z0-9_-]+=[^&\s\])\"']+)+[^\s\])\"']*",
    re.I,
)
_LONG_URL_RE = re.compile(r"https?://\S{100,}")

_URL_KEY_SUFFIXES = (
    "_url",
    "_link",
    "_href",
    "attachment_url",
    "photo_url",
    "proof_of_payment_url",
    "document_url",
    "media_url",
    "file_url",
)


def _is_url_key(key: str) -> bool:
    lowered = key.lower()
    return lowered.endswith(_URL_KEY_SUFFIXES) or lowered in {
        "url",
        "href",
        "link",
        "attachment",
    }


def _scrub_url_value(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return text
    if "X-Amz-" in text or len(text) > 120:
        return "[document on file — open in Finance or Documents]"
    return text


def sanitize_tool_payload_for_llm(payload: Any) -> Any:
    """Strip presigned URLs from tool JSON before the model echoes them to users."""
    if isinstance(payload, dict):
        cleaned: dict[str, Any] = {}
        for key, val in payload.items():
            # Keep storage keys / filenames; scrub only URL-like values.
            if key in ("storage_key", "filename", "mime_type", "secure_photo_refs"):
                if key == "secure_photo_refs" and isinstance(val, list):
                    cleaned[key] = [
                        {
                            "storage_key": (item.get("storage_key") or "") if isinstance(item, dict) else "",
                            "filename": (item.get("filename") or "photo.jpg") if isinstance(item, dict) else "photo.jpg",
                            "mime_type": (item.get("mime_type") or "image/jpeg") if isinstance(item, dict) else "image/jpeg",
                            "index": item.get("index") if isinstance(item, dict) else None,
                            "has_secure_url": bool(isinstance(item, dict) and item.get("url")),
                        }
                        for item in val
                    ]
                else:
                    cleaned[key] = val
            elif isinstance(val, str) and _is_url_key(key):
                cleaned[key] = _scrub_url_value(val)
            elif isinstance(val, (dict, list)):
                cleaned[key] = sanitize_tool_payload_for_llm(val)
            else:
                cleaned[key] = val
        return cleaned
    if isinstance(payload, list):
        return [sanitize_tool_payload_for_llm(item) for item in payload]
    return payload


def format_miya_reply(text: str | None) -> str:
    """Strip markdown markers and em-dashes; keep warm plain language."""
    if not text:
        return ""
    out = str(text).strip()
    out = _MARKDOWN_LINK_RE.sub(r"\1", out)
    out = _PRESIGNED_URL_RE.sub("[document on file — open in Finance or Documents]", out)
    out = _LONG_URL_RE.sub("[link]", out)
    out = _BOLD_RE.sub(r"\1", out)
    out = _ITALIC_RE.sub(r"\1", out)
    out = out.replace("—", ", ")
    out = out.replace("–", ", ")
    out = out.replace("---", "")
    out = _BULLET_RE.sub("", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    return out.strip()
