"""Memory priority — never let lower layers override higher ones."""
from __future__ import annotations

# Highest → lowest authority when facts conflict
MEMORY_PRIORITY = (
    "CURRENT_DATABASE_STATE",
    "RECENT_OPERATIONAL_EVENT",
    "STRUCTURED_OPERATIONAL_MEMORY",
    "DOCUMENT_DATA",
    "CONVERSATION_MEMORY",
    "SEMANTIC_HISTORICAL_RECALL",
)

PRIORITY_RANK = {name: i for i, name in enumerate(MEMORY_PRIORITY)}


def prefer_source(a: str, b: str) -> str:
    """Return the higher-authority source name."""
    ra = PRIORITY_RANK.get(a, 999)
    rb = PRIORITY_RANK.get(b, 999)
    return a if ra <= rb else b


def memory_priority_directive() -> str:
    return (
        "[MEMORY PRIORITY — NON-NEGOTIABLE]\n"
        "When information conflicts, prefer in this order:\n"
        "1. CURRENT DATABASE STATE (get_current_* / find_* tools)\n"
        "2. RECENT OPERATIONAL EVENT\n"
        "3. STRUCTURED OPERATIONAL MEMORY\n"
        "4. DOCUMENT DATA\n"
        "5. CONVERSATION MEMORY (what was just said)\n"
        "6. SEMANTIC HISTORICAL RECALL (rare; never for status)\n"
        "Never allow an old conversation to override current database state.\n"
        "Working memory holds focus pointers (ids) only — re-fetch status from DB.\n"
    )
