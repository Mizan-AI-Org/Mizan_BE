"""WhatsApp image/document → Miya tenant document pipeline."""

from __future__ import annotations

import logging

from accounts.rbac_enforce import user_can_use_miya
from core.whatsapp_config import get_miya_whatsapp_enabled
from miya.services.tenant_documents import serialize_tenant_document, store_tenant_document
from miya.services.whatsapp import enqueue_miya_whatsapp_turn
from notifications.media_persist import download_whatsapp_media
from notifications.services import notification_service

logger = logging.getLogger(__name__)


def _media_from_message(msg: dict) -> tuple[str | None, str, str, str]:
    image_obj = msg.get("image") or {}
    document_obj = msg.get("document") or {}
    media_id = image_obj.get("id") or document_obj.get("id")
    mime_type = image_obj.get("mime_type") or document_obj.get("mime_type") or ""
    filename = document_obj.get("filename") or ""
    caption = (image_obj.get("caption") or document_obj.get("caption") or "").strip()
    return media_id, mime_type, filename, caption


def try_miya_whatsapp_attachment(
    *,
    user,
    phone_digits: str,
    msg: dict,
    session,
) -> bool:
    """
    Idle-state image/document on WhatsApp → S3 + TenantDocument → Miya turn.
    Returns True when the message was accepted for Miya handling.
    """
    if not get_miya_whatsapp_enabled() or not user or not user_can_use_miya(user):
        return False
    if not session or getattr(session, "state", None) != "idle":
        return False

    media_id, mime_type, filename, caption = _media_from_message(msg)
    if not media_id:
        return False

    file_bytes, mime_type, filename_dl = download_whatsapp_media(media_id)
    if not file_bytes:
        notification_service.send_whatsapp_text(
            phone_digits,
            "I couldn't download that file. Please try sending it again.",
        )
        return True

    restaurant = getattr(user, "restaurant", None)
    if not restaurant:
        notification_service.send_whatsapp_text(
            phone_digits,
            "Your account has no workspace linked yet. Ask your manager for access.",
        )
        return True

    try:
        doc = store_tenant_document(
            restaurant=restaurant,
            uploaded_by=user,
            uploader_phone=phone_digits,
            source="WHATSAPP",
            file_bytes=file_bytes,
            filename=filename or filename_dl or "whatsapp-upload.bin",
            mime_type=mime_type or "",
            caption=caption,
        )
    except ValueError as exc:
        code = str(exc)
        if code == "file_too_large":
            notification_service.send_whatsapp_text(
                phone_digits,
                "That file is too large. Please send a file under 12 MB.",
            )
        elif code == "unsupported_type":
            notification_service.send_whatsapp_text(
                phone_digits,
                "That file type isn't supported yet. Try a photo, PDF, or Office document.",
            )
        else:
            notification_service.send_whatsapp_text(
                phone_digits,
                "I couldn't save that file. Please try again.",
            )
        return True
    except Exception:
        logger.exception("Miya WhatsApp attachment failed phone=%s", phone_digits)
        notification_service.send_whatsapp_text(
            phone_digits,
            "Something went wrong saving your document. Please try again.",
        )
        return True

    row = serialize_tenant_document(doc)
    user_message = caption or f"I shared a document: {row['title']}. Please review and remember it."
    session_hint = dict(getattr(session, "context", None) or {})
    session_hint["attachment_ids"] = [str(doc.id)]
    session.context = session_hint
    session.save(update_fields=["context"])

    enqueue_miya_whatsapp_turn(
        user=user,
        phone_digits=phone_digits,
        message_text=user_message,
        session=session,
    )
    return True
