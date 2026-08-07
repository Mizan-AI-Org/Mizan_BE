"""Phase 7 — Semantic Operational Search types."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class SearchMode(str, Enum):
    STRUCTURED = "STRUCTURED"  # clear entity + filters → find_*
    SEMANTIC = "SEMANTIC"  # conceptual / paraphrase → ranked retrieval
    HYBRID = "HYBRID"  # structured filter + conceptual rank
    EVENT = "EVENT"  # what happened / who handled → event history


class SearchDomain(str, Enum):
    INCIDENT = "incident"
    INVOICE = "invoice"
    TASK = "task"
    STAFF = "staff"
    DOCUMENT = "document"
    CHECKLIST = "checklist"
    MEETING = "meeting"
    REMINDER = "reminder"
    EVENT = "event"
    MIXED = "mixed"
    UNKNOWN = "unknown"


@dataclass
class SearchFilters:
    organization_id: str = ""
    establishment_id: str = ""
    status: str = ""
    vendor: str = ""
    staff_name: str = ""
    days: int | None = None
    since: str = ""
    date_from: str = ""
    date_to: str = ""
    category: str = ""
    q: str = ""
    conceptual_terms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ParsedSearchQuery:
    raw: str
    mode: SearchMode
    domain: SearchDomain
    filters: SearchFilters = field(default_factory=SearchFilters)
    reasons: list[str] = field(default_factory=list)
    confidence: str = "MEDIUM"

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "mode": self.mode.value,
            "domain": self.domain.value,
            "filters": self.filters.to_dict(),
            "reasons": self.reasons,
            "confidence": self.confidence,
        }


@dataclass
class SearchHit:
    domain: str
    id: str
    title: str
    snippet: str = ""
    score: float = 0.0
    source: str = "structured"  # structured | semantic | event | hybrid
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SearchResult:
    query: ParsedSearchQuery
    hits: list[SearchHit] = field(default_factory=list)
    reply: str = ""
    success: bool = True
    scoped: dict[str, Any] = field(default_factory=dict)
    strategy: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query.to_dict(),
            "hits": [h.to_dict() for h in self.hits],
            "reply": self.reply,
            "success": self.success,
            "scoped": self.scoped,
            "strategy": self.strategy,
        }
