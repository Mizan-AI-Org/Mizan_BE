"""Helpers for checklist photo-proof (Yes → send photo → next task)."""
from __future__ import annotations

from typing import Any


def task_requires_photo(task) -> bool:
    """True when this ShiftTask needs a photo after Yes."""
    if not task:
        return False
    vtype = str(getattr(task, "verification_type", "") or "").upper()
    if getattr(task, "verification_required", False) and vtype == "PHOTO":
        return True
    cfg = getattr(task, "branch_config", None) or {}
    if isinstance(cfg, dict):
        if cfg.get("requires_photo") is True:
            return True
        if str(cfg.get("verification_type") or "").upper() == "PHOTO":
            return True
    return False


def photo_prompt_for_task(task, user=None) -> str:
    from core.i18n import format_photo_prompt, get_effective_language

    restaurant = None
    try:
        shift = getattr(task, "shift", None)
        restaurant = getattr(getattr(shift, "schedule", None), "restaurant", None) or getattr(
            user, "restaurant", None
        )
    except Exception:
        restaurant = getattr(user, "restaurant", None)

    lang = get_effective_language(user=user, restaurant=restaurant)
    title = (getattr(task, "title", None) or "").strip()
    desc = (getattr(task, "description", None) or "").strip()
    return format_photo_prompt(lang, title=title, description=desc)


def arm_whatsapp_photo_await(*, phone: str, user, task, shift_id: str | None = None) -> None:
    """Set WhatsApp session so the next inbound image completes this checklist task."""
    from notifications.models import WhatsAppSession
    from scheduling.models import ShiftChecklistProgress

    phone_digits = "".join(filter(str.isdigit, str(phone or "")))
    if len(phone_digits) < 6:
        return
    session = WhatsAppSession.objects.filter(phone=phone_digits).first()
    if not session:
        session = WhatsAppSession.objects.create(phone=phone_digits, user=user)
    ctx = dict(session.context or {}) if isinstance(session.context, dict) else {}
    ctx["awaiting_verification_for_task_id"] = str(task.id)
    checklist = dict(ctx.get("checklist") or {})
    checklist["current_task_id"] = str(task.id)
    if shift_id:
        checklist["shift_id"] = str(shift_id)
    # Seed task list from Live Board progress so photo resume can find next
    if user and shift_id and not checklist.get("tasks"):
        prog = ShiftChecklistProgress.objects.filter(shift_id=shift_id, staff=user).first()
        if prog and prog.task_ids:
            checklist["tasks"] = list(prog.task_ids)
            checklist["responses"] = dict(prog.responses or {})
    ctx["checklist"] = checklist
    session.context = ctx
    session.state = "awaiting_task_photo"
    session.user = user or session.user
    session.save(update_fields=["context", "state", "user"])


def verification_fields_from_item(item: dict[str, Any]) -> dict[str, Any]:
    """Extract verification_* for ShiftTask create from a template task JSON item."""
    requires = bool(
        item.get("requires_photo")
        or item.get("verification_required")
        or str(item.get("verification_type") or "").upper() == "PHOTO"
    )
    vtype = str(item.get("verification_type") or ("PHOTO" if requires else "NONE")).upper()
    if requires:
        vtype = "PHOTO"
    return {
        "verification_required": requires,
        "verification_type": vtype if vtype in {
            "NONE", "PHOTO", "DOCUMENT", "SIGNATURE", "CHECKLIST",
            "SUPERVISOR_APPROVAL", "TEMPERATURE_LOG", "QUANTITY_COUNT",
        } else ("PHOTO" if requires else "NONE"),
        "requires_photo": requires,
    }


def apply_verification_fields_to_shift_task(task, item: dict[str, Any]) -> bool:
    """Sync photo-proof flags from a template step onto an existing ShiftTask."""
    vfields = verification_fields_from_item(item if isinstance(item, dict) else {})
    if not vfields.get("requires_photo"):
        return False
    if task_requires_photo(task):
        return False
    cfg = dict(getattr(task, "branch_config", None) or {})
    cfg["requires_photo"] = True
    cfg["verification_type"] = "PHOTO"
    task.branch_config = cfg
    task.verification_required = True
    task.verification_type = "PHOTO"
    task.save(update_fields=["branch_config", "verification_required", "verification_type"])
    return True


def _storage_key_from_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if raw.startswith(("http://", "https://")):
        return raw
    return raw.lstrip("/")


def persist_checklist_photo_from_whatsapp(
    *,
    media_id: str,
    task,
    user,
    mime_type: str | None = None,
) -> tuple[str | None, str, str]:
    """
    Download WhatsApp image and persist checklist proof.
    Returns (durable_url, storage_key, mime_type).
    """
    from notifications.media_persist import (
        FOLDER_CHECKLIST_EVIDENCE,
        MEDIA_CATEGORY_CHECKLIST_EVIDENCE,
        download_whatsapp_media,
        persist_bytes_to_storage,
        persist_whatsapp_media,
    )

    restaurant_id = getattr(user, "restaurant_id", None)
    if not restaurant_id and getattr(task, "shift_id", None):
        restaurant_id = getattr(getattr(task, "shift", None), "restaurant_id", None)

    durable_url: str | None = None
    resolved_mime = mime_type
    if media_id:
        durable_url, persisted_mime, _filename = persist_whatsapp_media(
            media_id,
            folder=FOLDER_CHECKLIST_EVIDENCE,
            filename_hint=f"checklist_{task.id}.jpg",
            restaurant_id=restaurant_id,
            media_category=MEDIA_CATEGORY_CHECKLIST_EVIDENCE,
        )
        resolved_mime = resolved_mime or persisted_mime

        if not durable_url:
            file_bytes, dl_mime, dl_name = download_whatsapp_media(media_id)
            if file_bytes:
                durable_url = persist_bytes_to_storage(
                    file_bytes,
                    filename=dl_name or f"checklist_{task.id}.jpg",
                    folder=FOLDER_CHECKLIST_EVIDENCE,
                    content_type=(resolved_mime or dl_mime or "image/jpeg"),
                    restaurant_id=restaurant_id,
                    media_category=MEDIA_CATEGORY_CHECKLIST_EVIDENCE,
                )
                resolved_mime = resolved_mime or dl_mime

    storage_key = _storage_key_from_url(durable_url or "")
    return durable_url, storage_key, (resolved_mime or "image/jpeg").split(";")[0].strip()
