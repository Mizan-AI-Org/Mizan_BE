"""
Miya Memory Facade (Phase 2) — six separated layers.

1. Conversation Memory
2. Working Memory
3. Semantic Memory
4. Operational Memory  (most important after live DB)
5. Document Knowledge
6. Event History

Priority: CURRENT_DATABASE_STATE > RECENT_OPERATIONAL_EVENT >
STRUCTURED_OPERATIONAL_MEMORY > DOCUMENT_DATA > CONVERSATION_MEMORY >
SEMANTIC_HISTORICAL_RECALL
"""
from __future__ import annotations

from typing import Any

from miya.services.intelligence.conversation_memory import load_conversation_memory
from miya.services.intelligence.memory_priority import (
    MEMORY_PRIORITY,
    memory_priority_directive,
)
from miya.services.intelligence.semantic_memory import load_semantic_memory
from miya.services.intelligence.working_memory import get_working_memory
from miya.services.message_pipeline import sanitize_history


class MemoryStore:
    """Turn-scoped bundle — conversation + pointers. Never authoritative for status."""

    def __init__(
        self,
        *,
        conversation_id: str,
        user_id: str,
        organization_id: str,
        history: list[dict[str, Any]] | None = None,
        user=None,
        restaurant=None,
    ):
        self.conversation_id = conversation_id
        self.user_id = user_id
        self.organization_id = organization_id
        self.user = user
        self.restaurant = restaurant
        self.history = sanitize_history(history)
        self.notes: list[str] = []

    def conversation_turns(self) -> list[dict[str, str]]:
        return list(self.history)

    def add_note(self, note: str) -> None:
        text = (note or "").strip()
        if text:
            self.notes.append(text[:500])

    def as_context_block(self) -> dict[str, Any]:
        return assemble_memory_bundle(
            history=self.history,
            conversation_id=self.conversation_id,
            user=self.user,
            restaurant=self.restaurant,
            notes=self.notes,
        )


def remember_entity_ids(
    *,
    kind: str,
    entity_ids: list[str],
    organization_id: str,
    user_id: str,
) -> None:
    """Store IDs only (not status) in the ephemeral working-set cache."""
    try:
        from miya.services.working_set import remember_entities

        remember_entities(
            kind=kind,
            entities=[{"id": i} for i in entity_ids if i],
            restaurant_id=organization_id,
            user_id=user_id,
        )
    except Exception:
        pass


def reality_overrides_memory() -> str:
    return memory_priority_directive()


def assemble_memory_bundle(
    *,
    history: list[dict[str, Any]] | None = None,
    conversation_id: str = "",
    user=None,
    restaurant=None,
    notes: list[str] | None = None,
    semantic_query: str = "",
    semantic_hits: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble all layers for session/prompt injection — clearly separated."""
    conversation = load_conversation_memory(
        history, conversation_id=conversation_id
    )
    working = get_working_memory(user=user, restaurant=restaurant)
    semantic = load_semantic_memory(query=semantic_query, hits=semantic_hits)
    return {
        "priority": list(MEMORY_PRIORITY),
        "directive": memory_priority_directive(),
        "layers": {
            "conversation_memory": conversation,
            "working_memory": working,
            "semantic_memory": semantic,
            "operational_memory": {
                "layer": "STRUCTURED_OPERATIONAL_MEMORY",
                "directive": (
                    "Load via recall_operational_memory / reconstruct timeline tools. "
                    "Backed by database entities + OperationalEvent rows (survives restart)."
                ),
            },
            "document_knowledge": {
                "layer": "DOCUMENT_DATA",
                "directive": "Load via get_current_document / query_document_intelligence.",
            },
            "event_history": {
                "layer": "RECENT_OPERATIONAL_EVENT",
                "directive": "Load via event history / recall_operational_memory.",
            },
        },
        "notes": list(notes or []),
        "authority": "layered",
        "rule": "CURRENT DATABASE STATE always wins over conversation and semantic recall.",
    }


def memory_prompt_block(bundle: dict[str, Any] | None) -> str:
    """Compact system-prompt section."""
    if not bundle:
        return "\n" + memory_priority_directive()
    lines = ["\n[MIYA MEMORY LAYERS]", memory_priority_directive().rstrip()]
    working = (bundle.get("layers") or {}).get("working_memory") or {}
    if working and not working.get("empty"):
        lines.append("[WORKING MEMORY POINTERS]")
        for key in (
            "establishment_name",
            "department",
            "current_task_label",
            "current_incident_label",
            "current_document_label",
            "current_invoice_label",
            "current_workflow",
        ):
            val = working.get(key)
            if val:
                lines.append(f"- {key}: {val}")
        lines.append(
            "Pointers only — call get_current_* before stating status."
        )
    conv = (bundle.get("layers") or {}).get("conversation_memory") or {}
    lines.append(
        f"[CONVERSATION MEMORY] {conv.get('turn_count', 0)} recent turn(s) in context "
        "(follow-ups / pronouns only)."
    )
    return "\n".join(lines) + "\n"
