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
