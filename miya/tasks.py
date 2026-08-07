"""Background tasks for Miya — async WhatsApp and long-running chat turns."""

from __future__ import annotations

import base64
import logging
from typing import Any

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    name="miya.tasks.run_miya_dashboard_chat",
    bind=True,
    max_retries=0,
    soft_time_limit=280,
    time_limit=300,
)
def run_miya_dashboard_chat(
    self,
    *,
    user_id: str,
    user_message: str,
    history: list[dict[str, str]] | None,
    channel: str = "dashboard",
    preferred_restaurant_id: str | None = None,
    access_token: str | None = None,
    want_voice: bool = False,
    attachment_ids: list[str] | None = None,
    session_hint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one dashboard Miya turn off the HTTP worker (Mastra can take 60–120s)."""
    from accounts.models import CustomUser
    from notifications.services import notification_service

    from .services.agent import run_miya_chat

    from core.i18n import get_effective_language, tr

    user = CustomUser.objects.filter(id=user_id, is_active=True).first()
    if not user:
        return {
            "error": "user_not_found",
            "reply": "Session expired. Please sign in again.",
        }

    lang = get_effective_language(user=user)
    try:
        result = run_miya_chat(
            user=user,
            access_token=access_token,
            user_message=user_message,
            history=history,
            channel=channel,
            preferred_restaurant_id=preferred_restaurant_id,
            attachment_ids=attachment_ids,
            session_hint=session_hint,
        )
    except RuntimeError as exc:
        logger.warning("run_miya_dashboard_chat runtime error user=%s: %s", user_id, exc)
        return {
            "error": str(exc)[:200],
            "reply": tr("miya.wa.temporarily_unavailable", lang),
        }
    except Exception as exc:
        logger.exception("run_miya_dashboard_chat failed user=%s", user_id)
        return {
            "error": str(exc)[:200],
            "reply": tr("miya.wa.unexpected_error", lang),
        }

    reply = (result.get("reply") or "").strip()
    lang = (result.get("session_context") or {}).get("language") or lang
    payload: dict[str, Any] = {
        "reply": reply or tr("miya.wa.idle_prompt", lang),
        "tool_trace": result.get("tool_trace") or [],
    }
    ctx = result.get("session_context")
    if isinstance(ctx, dict):
        payload["session_context"] = {
            "location_id": ctx.get("location_id"),
            "location_name": ctx.get("location_name"),
            "restaurant_id": ctx.get("restaurant_id"),
            "available_locations": ctx.get("available_locations") or [],
        }

    if want_voice and payload["reply"]:
        audio_bytes, mime = notification_service.synthesize_speech_bytes(payload["reply"])
        if audio_bytes:
            payload["audio"] = {
                "mime_type": mime or "audio/mpeg",
                "base64": base64.b64encode(audio_bytes).decode("ascii"),
            }

    return payload


@shared_task(name="miya.tasks.run_miya_whatsapp_turn_async", bind=True, max_retries=0)
def run_miya_whatsapp_turn_async(
    self,
    *,
    user_id: str,
    phone_digits: str,
    message_text: str,
    session_id: str,
    voice_reply: bool = False,
    inbound_wamid: str = "",
) -> dict:
    """
    Process one WhatsApp → Miya turn off the webhook hot path.
    Sends the reply via WhatsApp when the agent finishes.
    """
    from accounts.models import CustomUser
    from notifications.models import WhatsAppSession

    from .services.whatsapp import handle_miya_whatsapp_turn

    user = CustomUser.objects.filter(id=user_id, is_active=True).first()
    session = WhatsAppSession.objects.filter(id=session_id).first()
    if not user or not session:
        logger.warning(
            "run_miya_whatsapp_turn_async missing user=%s session=%s phone=%s",
            user_id,
            session_id,
            phone_digits,
        )
        return {"ok": False, "reason": "missing_user_or_session"}

    try:
        handled = handle_miya_whatsapp_turn(
            user=user,
            phone_digits=phone_digits,
            message_text=message_text,
            session=session,
            voice_reply=voice_reply,
            inbound_wamid=inbound_wamid or None,
        )
        return {"ok": True, "handled": handled}
    except Exception as exc:
        logger.exception("run_miya_whatsapp_turn_async failed phone=%s: %s", phone_digits, exc)
        from miya.services.whatsapp import _finish_inbound_wamid

        _finish_inbound_wamid(inbound_wamid or None, failed=True)
        return {"ok": False, "reason": str(exc)[:200]}
