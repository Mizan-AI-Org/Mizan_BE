"""Concept expansion for conceptual / paraphrase search (not vector-only)."""
from __future__ import annotations

# Domain concept → related terms for ranking / query expansion
CONCEPT_MAP: dict[str, list[str]] = {
    "freezer": ["freezer", "frigo", "fridge", "cold storage", "walk-in", "refrigerat", "congel"],
    "complaint": ["complaint", "complained", "complain", "customer said", "unhappy", "plainte"],
    "delivery": ["delivery", "late delivery", "deliveries", "livraison", "supplier late", "shipment"],
    "insurance": ["insurance", "assurance", "policy", "coverage", "liability"],
    "opening": ["opening", "opening checklist", "ouverture", "open checklist", "morning checklist"],
    "checklist": ["checklist", "check list", "standing checklist", "tasks list"],
    "payroll": ["payroll", "salary", "paie", "wage"],
    "invoice": ["invoice", "facture", "bill", "payable"],
    "incident": ["incident", "accident", "safety", "hazard", "concern"],
    "broken": ["broken", "broke", "damage", "damaged", "faulty", "out of order", "panne"],
    "staff": ["staff", "employee", "waiter", "chef", "team", "personnel"],
    "payment": ["payment", "paid", "payguard", "approval", "overdue payment"],
}

STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "me",
        "my",
        "our",
        "of",
        "to",
        "for",
        "from",
        "with",
        "about",
        "where",
        "what",
        "which",
        "who",
        "whom",
        "how",
        "when",
        "show",
        "find",
        "get",
        "list",
        "see",
        "tell",
        "please",
        "related",
        "regarding",
        "someone",
        "something",
        "their",
        "have",
        "haven't",
        "has",
        "hasn't",
        "completed",
        "happened",
        "handled",
        "last",
        "week",
        "today",
        "yesterday",
    }
)


def expand_concepts(text: str) -> list[str]:
    """Return expanded tokens for conceptual matching."""
    low = (text or "").lower()
    terms: list[str] = []
    for key, syns in CONCEPT_MAP.items():
        if key in low or any(s in low for s in syns if len(s) > 3):
            terms.extend(syns)
    # raw tokens
    for tok in _tokens(low):
        if tok not in STOPWORDS and len(tok) > 2:
            terms.append(tok)
            if tok in CONCEPT_MAP:
                terms.extend(CONCEPT_MAP[tok])
    # dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def conceptual_score(blob: str, terms: list[str]) -> float:
    """Simple relevance: fraction of concept terms present + density bonus."""
    if not terms:
        return 0.0
    low = (blob or "").lower()
    hits = sum(1 for t in terms if t and t in low)
    if hits == 0:
        return 0.0
    base = hits / max(len(terms), 1)
    # Prefer denser matches
    density = hits / max(len(low.split()) or 1, 1)
    return min(1.0, base * 0.85 + density * 0.15 + (0.1 if hits >= 2 else 0))


def _tokens(text: str) -> list[str]:
    import re

    return re.findall(r"[a-z0-9àâäéèêëïîôùûüç'\-]+", text or "", flags=re.I)
