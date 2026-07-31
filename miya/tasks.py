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
) -> dict[str, Any]:
    """Run one dashboard Miya turn off the HTTP worker (Mastra can take 60–120s)."""
    from accounts.models import CustomUser
    from notifications.services import notification_service

    from .services.agent import run_miya_chat

    user = CustomUser.objects.filter(id=user_id, is_active=True).first()
    if not user:
        return {
            "error": "user_not_found",
            "reply": "Session expired. Please sign in again.",
        }

    try:
        result = run_miya_chat(
            user=user,
            access_token=access_token,
            user_message=user_message,
            history=history,
            channel=channel,
            preferred_restaurant_id=preferred_restaurant_id,
        )
    except RuntimeError as exc:
        logger.warning("run_miya_dashboard_chat runtime error user=%s: %s", user_id, exc)
        return {
            "error": str(exc)[:200],
            "reply": "Miya is temporarily unavailable. Try again shortly.",
        }
    except Exception as exc:
        logger.exception("run_miya_dashboard_chat failed user=%s", user_id)
        return {
            "error": str(exc)[:200],
            "reply": "Something went wrong talking to Miya. Try again in a moment.",
        }

    reply = (result.get("reply") or "").strip()
    payload: dict[str, Any] = {
        "reply": reply or "I'm here. What would you like me to help with?",
        "tool_trace": result.get("tool_trace") or [],
    }

    if want_voice and payload["reply"]:
        audio_bytes, mime = notification_service.synthesize_speech_bytes(payload["reply"])
        if audio_bytes:
            payload["audio"] = {
                "mime_type": mime or "audio/mpeg",
                "base64": base64.b64encode(audio_bytes).decode("ascii"),
            }

    return payload


@shared_task(name="miya.tasks.run_miya_whatsapp_turn_async", bind=True, max_retries=1)
def run_miya_whatsapp_turn_async(
    self,
    *,
    user_id: str,
    phone_digits: str,
    message_text: str,
    session_id: str,
    voice_reply: bool = False,
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
        )
        return {"ok": True, "handled": handled}
    except Exception as exc:
        logger.exception("run_miya_whatsapp_turn_async failed phone=%s: %s", phone_digits, exc)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=5) from exc
        return {"ok": False, "reason": str(exc)[:200]}
