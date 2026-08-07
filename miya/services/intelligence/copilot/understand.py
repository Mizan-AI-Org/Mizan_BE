"""UNDERSTAND — unified turn classification for copilot routing."""
from __future__ import annotations

import re
from typing import Any

from miya.services.intelligence.planning.types import ClassifiedIntent, IntentClass
from miya.services.intelligence.unified_understand import unified_understand

# Intents that require action — search must never steal these.
MUTATION_INTENTS = frozenset(
    {
        IntentClass.COMPLETE,
        IntentClass.ASSIGN,
        IntentClass.APPROVE,
        IntentClass.REJECT,
        IntentClass.CREATE,
        IntentClass.ROUTE,
        IntentClass.REMIND,
        IntentClass.SCHEDULE,
        IntentClass.DELETE,
        IntentClass.UPLOAD,
        IntentClass.UPDATE,
    }
)

_BRIEFING = re.compile(
    r"\b("
    r"what\s+(?:happened|needs my attention|needs attention|is on my plate|"
    r"should i know|do i need to know)"
    r"|what\s+needs\s+(?:my\s+)?attention"
    r"|morning\s+brief(?:ing)?"
    r"|daily\s+brief(?:ing)?"
    r"|ops\s+brief(?:ing)?"
    r"|where\s+are\s+we\s+(?:at|today)"
    r"|attention\s+today"
    r")\b",
    re.I,
)

_OVERDUE = re.compile(r"\b(overdue|past due|late tasks?)\b", re.I)
_RESPONSIBILITY = re.compile(
    r"\b(who\s+is\s+responsible|who\s+handles|who\s+owns|responsible\s+for)\b",
    re.I,
)
_WHY_ROUTED = re.compile(
    r"\bwhy\s+(?:was|is)\s+.+\s+(?:routed|sent|escalated|assigned)\b",
    re.I,
)
_SHOW_PHOTO = re.compile(
    r"\b(show\s+(?:me\s+)?(?:the\s+)?(?:photo|picture|image)|"
    r"display\s+(?:the\s+)?(?:photo|picture|image))\b",
    re.I,
)
_INSURANCE_EXPIRY = re.compile(
    r"\b(when\s+does|expir|renewal|insurance\s+(?:expire|due))\b",
    re.I,
)
_INVOICE_UPLOAD = re.compile(
    r"\b(invoice\s+i\s+uploaded|uploaded\s+(?:invoice|yesterday)|"
    r"what\s+happened\s+with\s+(?:the\s+)?invoice)\b",
    re.I,
)
_TODAY_SUMMARY = re.compile(
    r"\b(what\s+happened\s+today|today'?s?\s+(?:summary|recap|events))\b",
    re.I,
)


def understand_turn(
    message: str,
    *,
    session_context: dict[str, Any] | None = None,
    multimodal: dict[str, Any] | None = None,
    channel: str = "dashboard",
) -> ClassifiedIntent:
    """Single UNDERSTAND entry — channel-agnostic via unified_understand (Phase 12)."""
    ch = (session_context or {}).get("channel") or channel or "dashboard"
    return unified_understand(
        message,
        channel=ch,
        session_context=session_context,
        multimodal=multimodal,
    )


def is_mutation_intent(classified: ClassifiedIntent) -> bool:
    return classified.intent in MUTATION_INTENTS


def is_briefing_query(message: str) -> bool:
    t = (message or "").strip()
    if not t:
        return False
    low = t.lower()
    # Entity-specific history — not a daily briefing
    if re.search(r"\bwhat\s+happened\s+(?:to|with|yesterday|last)\b", low):
        return False
    if re.search(r"\bwho\s+(?:changed|reassigned|completed|closed|approved|assigned)\b", low):
        return False
    if re.search(r"\bwhen\s+was\b.+\b(?:completed|assigned|reassigned|closed|created|uploaded)\b", low):
        return False
    if re.search(r"\bwhat\s+is\s+the\s+status\b", low):
        return False
    if _BRIEFING.search(t):
        return True
    needles = (
        "what needs my attention",
        "what needs attention",
        "what happened today",
        "where are we at",
        "morning briefing",
    )
    return any(n in t.lower() for n in needles)


def is_operational_search_query(message: str, classified: ClassifiedIntent) -> bool:
    """
    Read-only search — never route mutations here.
    Production bug guard: COMPLETE/ASSIGN/etc. always skip search.
    """
    t = (message or "").strip()
    if not t or len(t) < 4:
        return False
    # Historical read-only questions — not mutations even if classifier says COMPLETE
    if re.search(
        r"\b(when\s+was|who\s+(?:changed|reassigned|completed|closed|approved)|"
        r"what\s+happened\s+(?:to|with|yesterday|last))\b",
        t,
        re.I,
    ):
        return True
    if is_mutation_intent(classified):
        return False
    if _OVERDUE.search(t) or _RESPONSIBILITY.search(t) or _WHY_ROUTED.search(t):
        return True
    if _SHOW_PHOTO.search(t) or _INSURANCE_EXPIRY.search(t) or _INVOICE_UPLOAD.search(t):
        return True
    if _TODAY_SUMMARY.search(t):
        return True
    if re.search(r"\bwhat\s+happened|who\s+handled|who\s+changed|who\s+reassigned|"
                 r"when\s+was|what\s+is\s+the\s+status\b", t, re.I):
        return True
    return bool(
        re.search(
            r"\b(find|show|search|look\s*up|retrieve|what\s+happened|who\s+handled|"
            r"which\s+(?:staff|tasks?|invoices?)|documents?\s+related|invoices?\s+from|"
            r"need\s+approval|pending\s+invoices?)\b",
            t,
            re.I,
        )
    )


def routing_hint(message: str, classified: ClassifiedIntent) -> str:
    """Human-readable routing decision for traceability."""
    if is_mutation_intent(classified):
        return f"mutation:{classified.intent.value.lower()}"
    if is_briefing_query(message):
        return "briefing"
    if is_operational_search_query(message, classified):
        return "search"
    if classified.intent == IntentClass.QUERY and classified.entity_type.value == "staff":
        return "staff_lookup"
    if classified.intent == IntentClass.RETRIEVE:
        return "retrieve"
    return "agent"
