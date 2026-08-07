"""Event-history search — who handled / what happened."""
from __future__ import annotations

import re

from miya.services.intelligence.search.concepts import conceptual_score
from miya.services.intelligence.search.types import ParsedSearchQuery, SearchHit
from miya.services.ops.context import OpsContext

_ENTITY_HISTORY = re.compile(
    r"\bwhat\s+happened\s+(?:to|with|yesterday|last)\b|"
    r"\bwho\s+(?:changed|reassigned|completed|closed)\b|"
    r"\bwhen\s+was\b.+\b(?:completed|assigned|reassigned)\b|"
    r"\b\w+'s\s+photo",
    re.I,
)


def event_search(ctx: OpsContext, parsed: ParsedSearchQuery) -> list[SearchHit]:
    from miya.services.intelligence.event_history import get_event_history
    from miya.services.intelligence.operational_memory import recall_operational_memory

    days = parsed.filters.days or 14
    entity_type = ""
    if parsed.domain.value == "incident":
        entity_type = "incident"
    elif parsed.domain.value == "invoice":
        entity_type = "invoice"
    elif parsed.domain.value == "task":
        entity_type = "task"

    hits: list[SearchHit] = []
    raw_q = (parsed.raw or parsed.filters.q or "").strip()

    if raw_q and _ENTITY_HISTORY.search(raw_q):
        from miya.services.ops.history import get_entity_history

        hist = get_entity_history(ctx, entity_type=entity_type, q=raw_q)
        if getattr(hist, "success", False):
            for ev in (hist.data or {}).get("history") or []:
                if not isinstance(ev, dict):
                    continue
                blob = str(ev.get("summary") or ev.get("event_type") or "")
                hits.append(
                    SearchHit(
                        domain="event",
                        id=str(ev.get("id") or ""),
                        title=blob[:120] or "History event",
                        snippet=blob[:220],
                        score=0.85,
                        source="entity_history",
                        metadata={**ev, "entity_history": True},
                    )
                )
            current = (hist.data or {}).get("current_state")
            if isinstance(current, dict) and current.get("id"):
                hits.insert(
                    0,
                    SearchHit(
                        domain=parsed.domain.value or "task",
                        id=str(current.get("id") or ""),
                        title=str(current.get("title") or "Current state"),
                        snippet=f"Status: {current.get('status')}",
                        score=0.95,
                        source="current_state",
                        metadata={"current_state": current},
                    ),
                )

    hist = get_event_history(
        ctx,
        entity_type=entity_type,
        q=parsed.filters.q or parsed.raw,
        limit=30,
    )
    if getattr(hist, "success", False):
        events = (hist.data or {}).get("events") or (hist.data or {}).get("history") or []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            blob = " ".join(
                str(ev.get(k) or "")
                for k in ("summary", "event_type", "entity_label", "entity_type")
            )
            score = conceptual_score(blob, parsed.filters.conceptual_terms or [parsed.filters.q])
            if parsed.filters.q and parsed.filters.q.lower() in blob.lower():
                score = max(score, 0.4)
            if score < 0.08 and not parsed.filters.q:
                score = 0.2
            if score >= 0.08:
                hits.append(
                    SearchHit(
                        domain="event",
                        id=str(ev.get("id") or ev.get("entity_id") or ""),
                        title=str(ev.get("summary") or ev.get("event_type") or "Event"),
                        snippet=blob[:220],
                        score=round(score, 4),
                        source="event",
                        metadata=ev,
                    )
                )

    mem = recall_operational_memory(
        ctx,
        q=parsed.filters.q or parsed.raw,
        entity_type=entity_type,
        days=days,
    )
    if getattr(mem, "success", False):
        for row in (mem.data or {}).get("observations") or (mem.data or {}).get("events") or []:
            if not isinstance(row, dict):
                continue
            title = str(row.get("summary") or row.get("entity_label") or "Memory")
            hits.append(
                SearchHit(
                    domain="event",
                    id=str(row.get("id") or row.get("entity_id") or ""),
                    title=title,
                    snippet=str(row.get("summary") or "")[:220],
                    score=0.45,
                    source="event",
                    metadata=row,
                )
            )

    # Deduplicate by id+title
    seen: set[str] = set()
    out: list[SearchHit] = []
    for h in sorted(hits, key=lambda x: -x.score):
        key = f"{h.id}:{h.title}"
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out[:20]
