"""Conversation Memory — recent turns for pronoun / follow-up resolution.

Mastra thread/resource memory is the durable store when MIYA_AGENT_PROVIDER=mastra.
Django keeps a sanitized turn window for the OpenAI loop and WhatsApp session.
Never use this layer for entity status.
"""
from __future__ import annotations

from typing import Any

from miya.services.message_pipeline import sanitize_history


def load_conversation_memory(
    history: list[dict[str, Any]] | None,
    *,
    conversation_id: str = "",
    max_turns: int = 24,
) -> dict[str, Any]:
    turns = sanitize_history(history)[-max_turns:]
    return {
        "layer": "CONVERSATION_MEMORY",
        "authority": "conversation_only",
        "conversation_id": conversation_id or None,
        "turns": turns,
        "turn_count": len(turns),
        "mastra_thread": True,  # Mastra persists the same thread when enabled
        "directive": (
            "Use for: what the user just asked, which item they meant, "
            "'the second task'. Do NOT use for task/incident/invoice status."
        ),
    }
