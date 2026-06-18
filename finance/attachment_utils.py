"""Persist WhatsApp / agent-supplied invoice files on ``Invoice`` rows."""
from __future__ import annotations

import logging
import mimetypes
import re
from typing import TYPE_CHECKING

from django.core.files.base import ContentFile

if TYPE_CHECKING:
    from finance.models import Invoice

logger = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"[^a-zA-Z0-9._-]+")


def _guess_extension(content_type: str | None, filename_hint: str | None) -> str:
    ct = (content_type or "").lower().split(";")[0].strip()
    name = (filename_hint or "").lower()
    if name.endswith(".pdf") or ct == "application/pdf":
        return ".pdf"
    if name.endswith(".png") or ct == "image/png":
        return ".png"
    if name.endswith(".webp") or ct == "image/webp":
        return ".webp"
    if name.endswith(".gif") or ct == "image/gif":
        return ".gif"
    if name.endswith((".jpg", ".jpeg")) or ct in ("image/jpeg", "image/jpg"):
        return ".jpg"
    if name.endswith(".heic") or ct == "image/heic":
        return ".heic"
    ext = mimetypes.guess_extension(ct or "") if ct else None
    return ext or ".bin"


def save_invoice_attachment(
    invoice: Invoice,
    file_bytes: bytes,
    *,
    content_type: str | None = None,
    filename_hint: str | None = None,
) -> bool:
    """Store bytes on the invoice row. Returns True when saved."""
    if not file_bytes:
        return False

    ct = (content_type or "application/octet-stream").split(";")[0].strip()[:100]
    ext = _guess_extension(ct, filename_hint)
    base = (filename_hint or f"invoice_{invoice.id}").rsplit("/", 1)[-1]
    base = _SAFE_NAME.sub("_", base).strip("._") or f"invoice_{invoice.id}"
    if not base.lower().endswith(ext):
        base = f"{base}{ext}"

    try:
        invoice.attachment.save(base, ContentFile(file_bytes), save=False)
        invoice.attachment_content_type = ct
        invoice.attachment_filename = base[:255]
        update_fields = ["attachment", "attachment_content_type", "attachment_filename", "updated_at"]
        if ct.startswith("image/"):
            invoice.photo.save(base, ContentFile(file_bytes), save=False)
            update_fields.append("photo")
        invoice.save(update_fields=update_fields)
        return True
    except Exception:
        logger.exception("save_invoice_attachment failed for invoice=%s", invoice.id)
        return False


def attach_invoice_from_url(invoice: Invoice, url: str) -> bool:
    """Download a remote URL (e.g. WhatsApp media) and persist it."""
    from core.media_fetch import fetch_remote_media_bytes

    raw = (url or "").strip()
    if not raw:
        return False
    file_bytes, content_type = fetch_remote_media_bytes(raw)
    if not file_bytes:
        return False
    return save_invoice_attachment(
        invoice,
        file_bytes,
        content_type=content_type,
        filename_hint=raw.rsplit("/", 1)[-1].split("?")[0] or None,
    )
