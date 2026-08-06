"""Central Miya voice identity — Fish Audio primary, OpenAI female fallback."""
from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

# Fish Audio Voice Library — Sarah: warm, young female, cross-lingual on s2.1-pro
# (English, French, Arabic, Darija from reply text). Telnyx/Fish curated roster.
DEFAULT_FISH_REFERENCE_ID = "933563129e564b19a115bedd57b7406a"
DEFAULT_FISH_MODEL = "s2.1-pro"
DEFAULT_VOICE_LABEL = "Sarah"
DEFAULT_VOICE_SPEED = 1.05

# OpenAI TTS — female-presenting voices only (never alloy/echo/onyx/fable/ash).
ALLOWED_OPENAI_VOICES = frozenset({"shimmer", "nova", "coral", "sage"})
DEFAULT_OPENAI_VOICE = "shimmer"


@dataclass(frozen=True)
class MiyaVoiceSettings:
    reference_id: str
    model: str
    speed: float
    label: str
    openai_fallback_voice: str
    fish_configured: bool
    provider: str  # fish-audio | openai-fallback | none


def _platform_row():
    try:
        from platform_admin.whatsapp_services import get_singleton_config

        return get_singleton_config()
    except Exception:
        return None


def get_miya_voice_settings() -> MiyaVoiceSettings:
    """Resolve voice from Platform Admin DB → env → defaults."""
    row = _platform_row()
    env_ref = (getattr(settings, "FISH_AUDIO_REFERENCE_ID", "") or "").strip()
    env_model = (getattr(settings, "FISH_AUDIO_MODEL", "") or DEFAULT_FISH_MODEL).strip()
    env_speed = getattr(settings, "FISH_AUDIO_VOICE_SPEED", DEFAULT_VOICE_SPEED)
    env_label = (getattr(settings, "FISH_AUDIO_VOICE_LABEL", "") or DEFAULT_VOICE_LABEL).strip()
    env_openai = (getattr(settings, "OPENAI_TTS_VOICE", "") or DEFAULT_OPENAI_VOICE).strip().lower()

    db_ref = (getattr(row, "miya_fish_reference_id", "") or "").strip() if row else ""
    db_model = (getattr(row, "miya_fish_model", "") or "").strip() if row else ""
    db_label = (getattr(row, "miya_voice_label", "") or "").strip() if row else ""
    db_speed = getattr(row, "miya_voice_speed", None) if row else None
    db_openai = (getattr(row, "miya_openai_fallback_voice", "") or "").strip().lower() if row else ""

    reference_id = db_ref or env_ref or DEFAULT_FISH_REFERENCE_ID
    model = db_model or env_model or DEFAULT_FISH_MODEL
    label = db_label or env_label or DEFAULT_VOICE_LABEL

    try:
        speed = float(db_speed if db_speed is not None else env_speed)
    except (TypeError, ValueError):
        speed = DEFAULT_VOICE_SPEED
    speed = max(0.85, min(1.25, speed))

    openai_voice = db_openai or env_openai or DEFAULT_OPENAI_VOICE
    if openai_voice not in ALLOWED_OPENAI_VOICES:
        openai_voice = DEFAULT_OPENAI_VOICE

    fish_key = bool((getattr(settings, "FISH_AUDIO_API_KEY", "") or "").strip())
    if fish_key and reference_id:
        provider = "fish-audio"
    elif bool((getattr(settings, "OPENAI_API_KEY", "") or "").strip()):
        provider = "openai-fallback"
    else:
        provider = "none"

    return MiyaVoiceSettings(
        reference_id=reference_id,
        model=model,
        speed=speed,
        label=label,
        openai_fallback_voice=openai_voice,
        fish_configured=fish_key,
        provider=provider,
    )


def get_miya_openai_fallback_voice() -> str:
    return get_miya_voice_settings().openai_fallback_voice


def serialize_miya_voice_for_api() -> dict:
    """Public voice metadata for dashboard + platform admin."""
    cfg = get_miya_voice_settings()
    ref = cfg.reference_id
    masked_ref = f"{ref[:6]}…{ref[-4:]}" if len(ref) > 12 else ref
    return {
        "label": cfg.label,
        "reference_id": ref,
        "reference_id_masked": masked_ref,
        "model": cfg.model,
        "speed": cfg.speed,
        "openai_fallback_voice": cfg.openai_fallback_voice,
        "provider": cfg.provider,
        "fish_audio_configured": cfg.fish_configured,
    }
