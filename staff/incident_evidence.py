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


def arm_whatsapp_incident_photo_await(*, phone: str, user, ticket_id: str) -> bool:
    """Set WhatsApp session so the next inbound image attaches to this incident."""
    from notifications.models import WhatsAppSession

    phone_digits = "".join(filter(str.isdigit, str(phone or "")))
    if len(phone_digits) < 6 or not ticket_id:
        return False
    session = WhatsAppSession.objects.filter(phone=phone_digits).first()
    if not session:
        session = WhatsAppSession.objects.create(phone=phone_digits, user=user)
    ctx = dict(session.context or {}) if isinstance(session.context, dict) else {}
    ctx["incident_ticket_id"] = str(ticket_id)
    ctx.pop("pending_incident", None)
    session.context = ctx
    session.state = "awaiting_incident_photo"
    session.user = user or session.user
    session.save(update_fields=["context", "state", "user"])
    return True


def _resolve_public_url(raw: str) -> str:
    """Presign S3 keys; pass through absolute URLs."""
    from core.s3_storage import generate_presigned_url, s3_media_enabled

    text = (raw or "").strip()
    if not text:
        return ""
    if text.startswith(("http://", "https://")):
        return text
    key = text.lstrip("/")
    if s3_media_enabled():
        return generate_presigned_url(key) or ""
    try:
        from django.core.files.storage import default_storage

        return default_storage.url(key)
    except Exception:
        return f"/{key}"


def list_incident_photos(ticket) -> list[dict[str, Any]]:
    """Return photo attachments with secure (presigned) URLs for Miya/dashboard."""
    from core.s3_storage import file_field_download_url

    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(*, url: str, storage_key: str = "", filename: str = "", mime: str = "", caption: str = "") -> None:
        resolved = _resolve_public_url(url or storage_key)
        if not resolved or resolved in seen:
            return
        seen.add(resolved)
        items.append(
            {
                "url": resolved,
                "storage_key": (storage_key or url or "").lstrip("/"),
                "filename": filename or "photo.jpg",
                "mime_type": mime or "image/jpeg",
                "caption": caption or "",
            }
        )

    if ticket is None:
        return items

    try:
        if ticket.photo and ticket.photo.name:
            url = file_field_download_url(ticket.photo) or ""
            _add(
                url=url or ticket.photo.name,
                storage_key=ticket.photo.name,
                filename=ticket.photo.name.rsplit("/", 1)[-1],
                mime="image/jpeg",
            )
    except Exception:
        pass

    for idx, entry in enumerate(getattr(ticket, "photo_evidence", None) or [], start=1):
        if not isinstance(entry, dict):
            continue
        raw = (entry.get("storage_key") or entry.get("url") or "").strip()
        if not raw:
            continue
        _add(
            url=raw,
            storage_key=entry.get("storage_key") or raw,
            filename=(entry.get("filename") or f"Photo {idx}"),
            mime=(entry.get("mime_type") or "image/jpeg"),
            caption=(entry.get("caption") or ""),
        )
    return items


def load_incident_photo_bytes(ticket, *, index: int = 0) -> tuple[bytes, str, str] | None:
    """Load raw image bytes for WhatsApp outbound send. Returns (bytes, mime, filename)."""
    from django.core.files.storage import default_storage

    photos = list_incident_photos(ticket)
    if not photos:
        return None
    idx = max(0, min(index, len(photos) - 1))
    entry = photos[idx]
    mime = entry.get("mime_type") or "image/jpeg"
    filename = entry.get("filename") or "incident.jpg"
    key = (entry.get("storage_key") or "").lstrip("/")

    # Prefer ImageField open
    try:
        if index == 0 and ticket.photo and ticket.photo.name:
            ticket.photo.open("rb")
            try:
                data = ticket.photo.read()
            finally:
                ticket.photo.close()
            if data:
                return data, mime, filename
    except Exception:
        logger.exception("load_incident_photo_bytes: photo field read failed")

    if key:
        try:
            if default_storage.exists(key):
                with default_storage.open(key, "rb") as fh:
                    data = fh.read()
                if data:
                    return data, mime, filename
        except Exception:
            logger.exception("load_incident_photo_bytes: storage open failed key=%s", key)

    # Last resort: HTTP GET of resolved/presigned URL
    url = entry.get("url") or ""
    if url.startswith(("http://", "https://")):
        try:
            import requests

            resp = requests.get(url, timeout=45)
            if resp.status_code == 200 and resp.content:
                return resp.content, mime, filename
        except Exception:
            logger.exception("load_incident_photo_bytes: http fetch failed")
    return None


def notify_owners_photo_attached(ticket) -> None:
    """Ping assigned / category owners that photo evidence was added."""
    if not ticket:
        return
    try:
        from notifications.services import notification_service

        title = getattr(ticket, "title", None) or "Incident"
        msg = f"Photo evidence added to incident: {title}"
        targets = []
        if getattr(ticket, "assigned_to", None):
            targets.append(ticket.assigned_to)
        try:
            from staff.incident_routing import resolve_all_assignees_for_incident_type

            for u in resolve_all_assignees_for_incident_type(
                ticket.restaurant, getattr(ticket, "incident_type", None)
            ):
                if u and all(str(u.id) != str(t.id) for t in targets):
                    targets.append(u)
        except Exception:
            pass
        for u in targets[:8]:
            try:
                notification_service.send_custom_notification(
                    recipient=u,
                    message=msg,
                    title="Incident photo",
                    notification_type="INCIDENT",
                    channels=["app", "push"],
                    sender=None,
                )
            except Exception:
                pass
            phone = (getattr(u, "phone", None) or "").strip()
            if phone:
                try:
                    notification_service.send_whatsapp_text(
                        phone,
                        f"📷 {msg}\nOpen Incidents on the dashboard to review.",
                    )
                except Exception:
                    pass
    except Exception:
        logger.exception("notify_owners_photo_attached failed")
