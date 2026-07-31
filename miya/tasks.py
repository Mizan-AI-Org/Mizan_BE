"""Background tasks for Miya — async WhatsApp and long-running chat turns."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


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
