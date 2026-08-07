"""Intent helpers: board briefing vs entity status vs ambiguous pronoun."""
from __future__ import annotations

import re

# Board / ops overview — OK for list_operations_live fast path.
_BOARD_BRIEFING = re.compile(
    r"(?:"
    r"(?:what|which|show|list|any|tell me|give me|do i have|where are we).{0,40}"
    r"(?:pending|open|en attente|new demand|operations live|tasks?\s+for\s+today|at today)"
    r"|(?:pending|open)\s+tasks?"
    r"|tâches?\s+en\s+attente"
    r"|tasks?\s+(?:for|on)\s+today"
    r"|today'?s?\s+(?:open\s+)?tasks?"
    r"|what(?:'s|\s+is)\s+(?:on\s+)?operations\s+live"
    r"|where(?:'s|\s+are)\s+we\s+(?:at|with|on|today)"
    r"|status\s+update"
    r"|how(?:'s|\s+are)\s+(?:we|things|operations)"
    r"|morning\s+(?:brief|update|status|summary)"
    r"|(?:give me|need)\s+(?:a\s+)?(?:status|update|summary|briefing)"
    r"|où\s+en\s+sommes"
    r"|on\s+nous\s+en\s+est"
    r")",
    re.I,
)

# Named entity status — MUST retrieve from DB, never board summary alone.
_ENTITY_STATUS = re.compile(
    r"(?:"
    r"(?:is|has|was|did)\s+.+\s+(?:completed?|done|finished|started|accepted|cancelled|closed)"
    r"|(?:status|état|etat)\s+(?:of|de|du|des|for|pour)\s+\S+"
    r"|what(?:'s|\s+is)\s+the\s+status\s+of\s+\S+"
    r"|(?:check|look\s+up)\s+(?:the\s+)?(?:status|task|incident)\s+(?:of|for)\s+\S+"
    r"|est[- ]ce\s+que\s+.+\s+(?:est|a)\s+(?:terminé|fini|complété|fait)"
    r")",
    re.I,
)

_PRONOUN_ONLY_ASSIGN = re.compile(
    r"^\s*(?:assign|reassign|give|delegate|passe|assigne)\s+"
    r"(?:it|that|this|ça|ca|le|la|les)\s+"
    r"(?:to|à|a|au)\s+.+\s*$",
    re.I,
)

_MANAGER_ROLES = frozenset({"OWNER", "ADMIN", "SUPER_ADMIN", "MANAGER"})


def looks_like_board_briefing(message: str, role: str) -> bool:
    text = (message or "").strip()
    if not text or (role or "").upper() not in _MANAGER_ROLES:
        return False
    if looks_like_entity_status(text):
        return False
    if _BOARD_BRIEFING.search(text):
        return True
    lower = text.lower()
    if any(
        k in lower
        for k in (
            "where are we",
            "status update",
            "how are we",
            "morning briefing",
            "operations snapshot",
            "where we at",
            "what's urgent",
            "what is urgent",
            "où en sommes",
            "on nous en est",
        )
    ):
        return True
    if len(text.split()) <= 6 and any(
        k in lower for k in ("pending", "open", "attente", "demands", "today")
    ):
        # bare "status?" alone is ambiguous — treat as briefing only if no "of"
        if "status" in lower and " of " not in lower and " de " not in lower:
            return True
        if "status" not in lower:
            return True
    return False


_STATUS_WRITE = re.compile(
    r"\b(change|set|mark|update|make|mettre|passe[rz]?|change[rz]?)\b.+\b"
    r"(completed?|done|finished|in[\s-]?progress|pending|cancelled?)\b",
    re.I,
)


def looks_like_status_write(message: str) -> bool:
    """'Change Ahmed's task to completed' — write, not read-only status."""
    return bool(_STATUS_WRITE.search((message or "").strip()))


def looks_like_entity_status(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    if looks_like_status_write(text):
        return False
    if _ENTITY_STATUS.search(text):
        return True
    lower = text.lower()
    # "Is Ahmed's task completed?"
    if re.search(r"\b(completed?|done|finished|in progress|pending)\b", lower) and re.search(
        r"\b(task|incident|checklist|demande)\b", lower
    ):
        return True
    if "status of" in lower or "état de" in lower or "etat de" in lower:
        return True
    return False


def extract_status_query_subject(message: str) -> str:
    """Best-effort subject for status lookup (name or title fragment)."""
    text = (message or "").strip()
    m = re.search(
        r"(?:status|état|etat)\s+(?:of|de|du|des|for|pour)\s+(.+?)(?:\?|$)",
        text,
        re.I,
    )
    if m:
        return m.group(1).strip(" ?.!")
    m = re.search(
        r"(?:is|has)\s+(.+?)(?:'s|’s)?\s+(?:task|incident|checklist).*(?:completed?|done|finished)",
        text,
        re.I,
    )
    if m:
        return m.group(1).strip()
    m = re.search(r"(.+?)(?:'s|’s)\s+task", text, re.I)
    if m:
        return m.group(1).strip()
    return ""


def looks_like_pronoun_assign(message: str) -> bool:
    return bool(_PRONOUN_ONLY_ASSIGN.match((message or "").strip()))
