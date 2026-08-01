"""WhatsApp inbound → Miya (Fish Audio voice) on Mizan platform number +212784476751."""

from __future__ import annotations

import logging

from accounts.rbac_enforce import user_can_use_miya
from core.whatsapp_config import get_miya_whatsapp_enabled, get_miya_whatsapp_voice_default
from miya.services.agent import run_miya_chat
from notifications.services import notification_service

logger = logging.getLogger(__name__)

HISTORY_KEY = "miya_chat_history"
MAX_HISTORY = 12


def _miya_whatsapp_enabled() -> bool:
    return get_miya_whatsapp_enabled()


def _load_history(session) -> list[dict[str, str]]:
    ctx = getattr(session, "context", None) or {}
    raw = ctx.get(HISTORY_KEY) or []
    if not isinstance(raw, list):
        return []
    out = []
    for turn in raw[-MAX_HISTORY:]:
        if isinstance(turn, dict) and turn.get("role") in ("user", "assistant"):
            content = (turn.get("content") or "").strip()
            if content:
                out.append({"role": turn["role"], "content": content})
    return out


def _save_history(session, history: list[dict[str, str]]) -> None:
    ctx = dict(getattr(session, "context", None) or {})
    ctx[HISTORY_KEY] = history[-MAX_HISTORY:]
    session.context = ctx
    session.save(update_fields=["context"])


def _send_miya_reply(phone_digits: str, reply: str, *, voice: bool = False) -> bool:
    """Deliver Miya reply on WhatsApp — text and optional Fish Audio voice note."""
    text = (reply or "").strip()
    if not text:
        return False

    if voice:
        audio_bytes, mime = notification_service.synthesize_speech_bytes(text)
        if audio_bytes:
            sent_ok, _info = notification_service.send_whatsapp_audio(
                phone=phone_digits,
                audio_bytes=audio_bytes,
                mime_type=mime or "audio/mpeg",
                voice_note=True,
            )
            if sent_ok:
                return True

    ok, info = notification_service.send_whatsapp_text(phone_digits, text)
    if not ok:
        logger.error(
            "Miya WhatsApp reply failed to send phone=%s info=%s",
            phone_digits,
            info,
        )
    return bool(ok)


def _celery_workers_available() -> bool:
    """True only when at least one Celery worker answers ping (avoid silent queue drops)."""
    try:
        from celery import current_app

        inspector = current_app.control.inspect(timeout=0.8)
        if inspector is None:
            return False
        ping = inspector.ping() or {}
        return bool(ping)
    except Exception as exc:
        logger.warning("Celery worker ping failed: %s", exc)
        return False


def enqueue_miya_whatsapp_turn(
    *,
    user,
    phone_digits: str,
    message_text: str,
    session,
    voice_reply: bool = False,
) -> bool:
    """
    Queue Miya WhatsApp processing off the webhook hot path when MIYA_ASYNC_CHAT is enabled
    and a Celery worker is alive. Otherwise run sync so the user always gets a reply.
    Returns True when the message was accepted for handling (sync or async).
    """
    from django.conf import settings

    use_async = bool(getattr(settings, "MIYA_ASYNC_CHAT", True))
    if use_async and not user:
        # Async task requires a user_id; unknown numbers always run sync invite reply.
        use_async = False
    if use_async and not _celery_workers_available():
        logger.warning(
            "MIYA_ASYNC_CHAT on but no Celery workers — sync fallback phone=%s",
            phone_digits,
        )
        use_async = False

    if not use_async:
        return handle_miya_whatsapp_turn(
            user=user,
            phone_digits=phone_digits,
            message_text=message_text,
            session=session,
            voice_reply=voice_reply,
        )

    from miya.tasks import run_miya_whatsapp_turn_async

    try:
        run_miya_whatsapp_turn_async.delay(
            user_id=str(user.id),
            phone_digits=phone_digits,
            message_text=message_text,
            session_id=str(session.id),
            voice_reply=voice_reply,
        )
        return True
    except Exception as exc:
        logger.warning(
            "Miya WhatsApp async queue failed (%s) — falling back to sync phone=%s",
            exc,
            phone_digits,
        )
        return handle_miya_whatsapp_turn(
            user=user,
            phone_digits=phone_digits,
            message_text=message_text,
            session=session,
            voice_reply=voice_reply,
        )


def handle_miya_whatsapp_turn(
    *,
    user,
    phone_digits: str,
    message_text: str,
    session,
    voice_reply: bool = False,
) -> bool:
    """
    Run Miya for one WhatsApp message. Returns True if handled (caller should continue).

    Uses the shared Mizan WhatsApp number; tenant + RBAC come from the resolved user.
    """
    if not _miya_whatsapp_enabled():
        return False

    if not user:
        _send_miya_reply(
            phone_digits,
            "Please ask your manager for a Mizan invite link so I can recognize your number. "
            "Open the link on this phone and send the prefilled activation message.",
        )
        return True

    if not user_can_use_miya(user):
        # Last-chance: pending ONE-TAP record may still need activation this turn
        # (e.g. phone matched an older user without Miya apps).
        try:
            from accounts.services import try_activate_staff_on_inbound_message

            activated = try_activate_staff_on_inbound_message(phone_digits)
            if activated and user_can_use_miya(activated):
                user = activated
            else:
                _send_miya_reply(
                    phone_digits,
                    "I recognize this number, but Miya isn't enabled for this account yet. "
                    "Ask your manager to share the staff activation link "
                    "(https://api.heymizan.ai/api/go/wa) and send the prefilled message.",
                )
                return True
        except Exception:
            _send_miya_reply(
                phone_digits,
                "I recognize this number, but Miya isn't enabled for this account yet. "
                "Ask your manager to share the staff activation link and try again.",
            )
            return True

    text = (message_text or "").strip()
    if not text:
        return False

    history = _load_history(session)
    session_hint = dict(getattr(session, "context", None) or {})
    if getattr(user, "restaurant_id", None):
        session_hint.setdefault("restaurant_id", str(user.restaurant_id))
    attachment_ids = session_hint.pop("attachment_ids", None)
    session_hint["thread_id"] = f"wa-{getattr(session, 'id', phone_digits)}"
    session_hint["whatsapp_session_id"] = session_hint["thread_id"]
    if attachment_ids:
        session.context = session_hint
        session.save(update_fields=["context"])

    try:
        result = run_miya_chat(
            user=user,
            access_token=None,
            user_message=text,
            history=history,
            channel="whatsapp",
            preferred_restaurant_id=session_hint.get("restaurant_id"),
            session_hint=session_hint,
            attachment_ids=attachment_ids,
        )
    except RuntimeError as exc:
        logger.exception("Miya WhatsApp chat failed for %s: %s", phone_digits, exc)
        _send_miya_reply(
            phone_digits,
            "Miya is temporarily unavailable. Try again shortly or use the Mizan dashboard.",
        )
        return True
    except Exception as exc:
        logger.exception("Miya WhatsApp unexpected error for %s: %s", phone_digits, exc)
        _send_miya_reply(
            phone_digits,
            "Something went wrong on my side. Please try again in a moment.",
        )
        return True

    reply = (result.get("reply") or "").strip()
    if not reply:
        _send_miya_reply(
            phone_digits,
            "I couldn't process that message. Please try again in a moment.",
        )
        return True

    history.append({"role": "user", "content": text})
    history.append({"role": "assistant", "content": reply})
    ctx = dict(getattr(session, "context", None) or {})
    ctx[HISTORY_KEY] = history[-MAX_HISTORY:]
    tenant_id = (result.get("session_context") or {}).get("restaurant_id")
    if tenant_id:
        ctx["tenant_id"] = tenant_id
        ctx["restaurant_id"] = tenant_id
    session.context = ctx
    session.save(update_fields=["context"])

    use_voice = voice_reply or get_miya_whatsapp_voice_default()
    _send_miya_reply(phone_digits, reply, voice=use_voice)
    return True
