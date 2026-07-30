"""Durable persistence for WhatsApp media (checklist / task proof / incidents).

Downloads Meta media by id and stores bytes via Django's default storage
(local MEDIA_ROOT or S3 when AWS_STORAGE_BUCKET_NAME is set).
"""
from __future__ import annotations

import logging
import mimetypes
import re
import uuid
from typing import Optional, Tuple

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

from core.storage_paths import org_media_folder

logger = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"[^a-zA-Z0-9._-]+")

# Legacy flat folders (local dev / pre-migration objects). Prefer *restaurant_id*.
FOLDER_TASK_PROOFS = "task-proofs"
FOLDER_CHECKLIST_EVIDENCE = "checklist-evidence"
FOLDER_INCIDENTS = "incidents"

MEDIA_CATEGORY_TASK_PROOFS = "tasks/proofs"
MEDIA_CATEGORY_CHECKLIST_EVIDENCE = "checklists/evidence"
MEDIA_CATEGORY_INCIDENTS = "incidents"

_EPHEMERAL_HOST_MARKERS = (
    "graph.facebook.com",
    "lookaside.fbsbx.com",
    "fbcdn.net",
    "scontent.",
)


def resolve_persist_folder(
    category: str,
    *,
    restaurant_id=None,
    legacy_folder: str | None = None,
) -> str:
    """Org-scoped S3 prefix when *restaurant_id* is known; else legacy flat folder."""
    if restaurant_id:
        return org_media_folder(restaurant_id, category)
    return (legacy_folder or category).strip("/")


def _ext_for_mime(mime_type: Optional[str], filename_hint: Optional[str] = None) -> str:
    ct = (mime_type or "").lower().split(";")[0].strip()
    name = (filename_hint or "").lower()
    if "png" in ct or name.endswith(".png"):
        return ".png"
    if "gif" in ct or name.endswith(".gif"):
        return ".gif"
    if "webp" in ct or name.endswith(".webp"):
        return ".webp"
    if "heic" in ct or name.endswith(".heic"):
        return ".heic"
    if "jpeg" in ct or "jpg" in ct or name.endswith((".jpg", ".jpeg")):
        return ".jpg"
    if ct.startswith("image/"):
        guessed = mimetypes.guess_extension(ct)
        if guessed:
            return guessed
    return ".jpg"


def _safe_filename(filename: str, *, mime_type: Optional[str] = None) -> str:
    base = (filename or "media").rsplit("/", 1)[-1]
    base = _SAFE_NAME.sub("_", base).strip("._") or "media"
    ext = _ext_for_mime(mime_type, base)
    if not base.lower().endswith(ext):
        if "." in base:
            stem = base.rsplit(".", 1)[0]
            base = f"{stem}{ext}"
        else:
            base = f"{base}{ext}"
    return base[:200]


def looks_like_ephemeral_whatsapp_url(url: str) -> bool:
    """True when *url* is likely a short-lived Meta/WhatsApp CDN link."""
    raw = (url or "").strip().lower()
    if not raw:
        return False
    return any(marker in raw for marker in _EPHEMERAL_HOST_MARKERS)


def download_whatsapp_media(media_id: str) -> Tuple[Optional[bytes], Optional[str], Optional[str]]:
    """Download WhatsApp Cloud media by id.

    Returns ``(bytes, mime_type, filename)``. Any element may be None on failure.
    """
    mid = (media_id or "").strip()
    if not mid:
        return None, None, None

    try:
        from notifications.services import notification_service

        media_url, mime_type = notification_service.fetch_whatsapp_media_url(mid)
        if not media_url:
            logger.warning("download_whatsapp_media: no URL for media_id=%s", mid)
            return None, None, None
        file_bytes = notification_service.download_media_bytes(media_url)
        if not file_bytes:
            logger.warning("download_whatsapp_media: empty download for media_id=%s", mid)
            return None, mime_type, None
        ext = _ext_for_mime(mime_type)
        filename = f"wa_{mid[:24]}{ext}"
        return file_bytes, mime_type or "image/jpeg", filename
    except Exception:
        logger.exception("download_whatsapp_media failed for media_id=%s", mid)
        return None, None, None


def persist_bytes_to_storage(
    file_bytes: bytes,
    *,
    filename: str,
    folder: str,
    content_type: Optional[str] = None,
    restaurant_id=None,
    media_category: str | None = None,
) -> Optional[str]:
    """Save *file_bytes* under *folder* via default storage; return a media URL."""
    if not file_bytes:
        return None

    if media_category:
        folder = resolve_persist_folder(
            media_category,
            restaurant_id=restaurant_id,
            legacy_folder=folder,
        )

    folder_clean = (folder or "uploads").strip("/").replace("..", "")
    safe_name = _safe_filename(filename, mime_type=content_type)
    stamp = timezone.now().strftime("%Y%m%d%H%M%S")
    unique = uuid.uuid4().hex[:8]
    if "." in safe_name:
        stem, ext = safe_name.rsplit(".", 1)
        stored_name = f"{stem}_{stamp}_{unique}.{ext}"
    else:
        stored_name = f"{safe_name}_{stamp}_{unique}"
    path = f"{folder_clean}/{stored_name}"

    try:
        content = ContentFile(file_bytes)
        try:
            content.content_type = (content_type or "application/octet-stream").split(";")[0].strip()
        except Exception:
            pass
        saved = default_storage.save(path, content)
        url = default_storage.url(saved)
        return url
    except Exception:
        logger.exception(
            "persist_bytes_to_storage failed folder=%s filename=%s",
            folder_clean,
            safe_name,
        )
        return None


def persist_whatsapp_media(
    media_id: str,
    *,
    folder: str,
    filename_hint: Optional[str] = None,
    restaurant_id=None,
    media_category: str | None = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Download Meta media and persist. Returns ``(url, mime_type, filename)``."""
    file_bytes, mime_type, filename = download_whatsapp_media(media_id)
    if not file_bytes:
        return None, mime_type, filename
    name = filename_hint or filename or f"wa_{media_id}.jpg"
    url = persist_bytes_to_storage(
        file_bytes,
        filename=name,
        folder=folder,
        content_type=mime_type or "image/jpeg",
        restaurant_id=restaurant_id,
        media_category=media_category,
    )
    return url, mime_type, name


def persist_remote_or_whatsapp_url(
    url: str,
    *,
    folder: str,
    filename_hint: Optional[str] = None,
    restaurant_id=None,
    media_category: str | None = None,
) -> Optional[str]:
    """If *url* is ephemeral WhatsApp/Meta media, re-host it; else return *url*."""
    raw = (url or "").strip()
    if not raw:
        return None
    if not looks_like_ephemeral_whatsapp_url(raw):
        return raw

    try:
        from core.media_fetch import fetch_remote_media_bytes

        file_bytes, content_type = fetch_remote_media_bytes(raw)
        if not file_bytes:
            return None
        hint = filename_hint or raw.rsplit("/", 1)[-1].split("?")[0] or "wa_media.jpg"
        return persist_bytes_to_storage(
            file_bytes,
            filename=hint,
            folder=folder,
            content_type=content_type or "image/jpeg",
            restaurant_id=restaurant_id,
            media_category=media_category,
        )
    except Exception:
        logger.exception("persist_remote_or_whatsapp_url failed")
        return None
