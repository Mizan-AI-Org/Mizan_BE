"""
Phase 12 — unified intent understanding across channels.

Same semantic request → same ClassifiedIntent regardless of dashboard/whatsapp/mobile/voice.
"""
from __future__ import annotations

from typing import Any

from miya.services.intelligence.paraphrase_lexicon import apply_paraphrase_lexicon, normalize_channel
from miya.services.intelligence.planning.classify import classify_message
from miya.services.intelligence.planning.types import ClassifiedIntent


def unified_understand(
    message: str,
    *,
    channel: str = "dashboard",
    session_context: dict[str, Any] | None = None,
    multimodal: dict[str, Any] | None = None,
) -> ClassifiedIntent:
    """
    Channel-agnostic UNDERSTAND step.

    1. Normalize channel metadata into session context
    2. Base classify_message()
    3. Structured paraphrase lexicon overlay
    """
    ctx = dict(session_context or {})
    ctx["channel"] = normalize_channel(channel)
    ctx.setdefault("input_channel", ctx["channel"])

    classified = classify_message(
        message,
        session_context=ctx,
        multimodal=multimodal,
    )
    classified = apply_paraphrase_lexicon(classified)
    classified.slots["input_channel"] = ctx["channel"]
    if "paraphrase:" not in " ".join(classified.reasons):
        classified.reasons.append(f"channel:{ctx['channel']}")
    return classified
