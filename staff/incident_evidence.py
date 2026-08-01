"""Persist and resolve incident photo / file evidence."""

from __future__ import annotations

import logging
from typing import Any

from django.core.files.base import ContentFile
from django.utils import timezone

logger = logging.getLogger(__name__)


def incident_has_photo_evidence(ticket) -> bool:
    if not ticket:
        return False
    try:
        if ticket.photo and ticket.photo.name:
            return True
    except Exception:
        pass
    evidence = getattr(ticket, "photo_evidence", None) or []
    return bool(evidence)


def _resolve_storage_key(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if raw.startswith(("http://", "https://")):
        return raw
    return raw.lstrip("/")


def append_incident_photo_evidence(
    ticket,
    *,
    file_bytes: bytes,
    mime_type: str = "",
    filename: str = "",
    media_id: str = "",
    caption: str = "",
    source: str = "whatsapp",
    submitted_by=None,
) -> dict[str, Any] | None:
    """
    Persist image bytes to storage, save primary photo on ticket, append to photo_evidence JSON.
    Returns the evidence entry dict or None on failure.
    """
    from notifications.media_persist import (
        FOLDER_INCIDENTS,
        MEDIA_CATEGORY_INCIDENTS,
        persist_bytes_to_storage,
    )

    if not ticket or not file_bytes:
        return None

    restaurant_id = getattr(ticket, "restaurant_id", None)
    mime = (mime_type or "image/jpeg").split(";")[0].strip()
    name = (filename or f"incident_{ticket.id}.jpg").strip()
    if not name.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic")):
        if "png" in mime:
            name = f"{name.rsplit('.', 1)[0]}.png" if "." in name else f"{name}.png"
        else:
            name = f"{name.rsplit('.', 1)[0]}.jpg" if "." in name else f"{name}.jpg"

    durable_url = persist_bytes_to_storage(
        file_bytes,
        filename=name,
        folder=FOLDER_INCIDENTS,
        content_type=mime,
        restaurant_id=restaurant_id,
        media_category=MEDIA_CATEGORY_INCIDENTS,
    )
    if not durable_url:
        return None

    storage_key = _resolve_storage_key(durable_url)
    submitted_at = timezone.now().isoformat()
    entry = {
        "url": durable_url,
        "storage_key": storage_key,
        "media_id": media_id or "",
        "mime_type": mime,
        "filename": name,
        "caption": (caption or "").strip(),
        "submitted_at": submitted_at,
        "source": source,
        "staff_id": str(submitted_by.id) if submitted_by else "",
    }

    evidence = list(getattr(ticket, "photo_evidence", None) or [])
    evidence.append(entry)
    ticket.photo_evidence = evidence

    # Keep ImageField populated for legacy UIs (first photo only)
    if not ticket.photo or not ticket.photo.name:
        try:
            ticket.photo.save(name, ContentFile(file_bytes), save=False)
        except Exception:
            logger.exception("append_incident_photo_evidence: photo field save failed")

    ticket.save(update_fields=["photo", "photo_evidence", "updated_at"])
    return entry


def append_incident_file_attachment(
    ticket,
    *,
    file_bytes: bytes,
    mime_type: str = "",
    filename: str = "",
    source: str = "whatsapp",
) -> bool:
    """Persist a non-image attachment on the incident."""
    if not ticket or not file_bytes:
        return False
    name = (filename or f"incident_{ticket.id}").strip()[:255]
    mime = (mime_type or "application/octet-stream")[:100]
    try:
        ticket.attachment.save(name, ContentFile(file_bytes), save=False)
        ticket.attachment_filename = name
        ticket.attachment_content_type = mime
        ticket.save(update_fields=["attachment", "attachment_filename", "attachment_content_type", "updated_at"])
        return True
    except Exception:
        logger.exception("append_incident_file_attachment failed ticket=%s", getattr(ticket, "id", None))
        return False
