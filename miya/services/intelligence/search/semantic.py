"""Conceptual / semantic ranking — not vector-for-everything.

Uses concept expansion + token relevance over scoped candidates.
Optional embedding hook only when MIYA_SEMANTIC_RECALL_ENABLED and hits provided.
"""
from __future__ import annotations

from typing import Any

from miya.services.intelligence.search.concepts import conceptual_score
from miya.services.intelligence.search.types import ParsedSearchQuery, SearchHit
from miya.services.ops.context import OpsContext


def semantic_rerank(
    ctx: OpsContext,
    parsed: ParsedSearchQuery,
    candidates: list[SearchHit],
    *,
    min_score: float = 0.08,
) -> list[SearchHit]:
    """Rank candidates by conceptual relevance. Does not invent rows outside candidates."""
    terms = list(parsed.filters.conceptual_terms or [])
    if parsed.filters.q and parsed.filters.q.lower() not in terms:
        terms.append(parsed.filters.q.lower())
    if not candidates:
        # Broad conceptual pull from structured multi-domain, then rank
        from miya.services.intelligence.search.structured import structured_search
        from miya.services.intelligence.search.types import SearchDomain

        broad = ParsedSearchQuery(
            raw=parsed.raw,
            mode=parsed.mode,
            domain=SearchDomain.MIXED if parsed.domain.value == "unknown" else parsed.domain,
            filters=parsed.filters,
            reasons=parsed.reasons + ["semantic_candidate_pull"],
        )
        candidates, _ = structured_search(ctx, broad)

    ranked: list[SearchHit] = []
    for hit in candidates:
        blob = " ".join(
            [
                hit.title,
                hit.snippet,
                str(hit.metadata.get("description") or ""),
                str(hit.metadata.get("vendor") or hit.metadata.get("vendor_name") or ""),
                str(hit.metadata.get("category") or ""),
                str(hit.metadata.get("incident_type") or ""),
            ]
        )
        score = conceptual_score(blob, terms)
        if score < min_score and parsed.filters.q:
            # fallback substring
            if parsed.filters.q.lower() in blob.lower():
                score = max(score, 0.35)
        if score >= min_score:
            ranked.append(
                SearchHit(
                    domain=hit.domain,
                    id=hit.id,
                    title=hit.title,
                    snippet=hit.snippet,
                    score=round(score, 4),
                    source="hybrid" if hit.source == "structured" else "semantic",
                    metadata=hit.metadata,
                )
            )
    ranked.sort(key=lambda h: -h.score)

    # Optional advisory semantic memory (never for status)
    try:
        from miya.services.intelligence.semantic_memory import (
            load_semantic_memory,
            semantic_recall_enabled,
        )

        if semantic_recall_enabled():
            mem = load_semantic_memory(query=parsed.raw, hits=None)
            _ = mem  # packaged for callers; not mixed into operational hits as authority
    except Exception:
        pass

    return ranked


def conceptual_incident_search(ctx: OpsContext, parsed: ParsedSearchQuery) -> list[SearchHit]:
    """
    'Find the incident where someone complained about the freezer.'
    Pull open+recent incidents then conceptually rank.
    """
    from miya.services.ops.incidents import find_incidents

    result = find_incidents(
        ctx,
        q="",
        status="ALL",
        days=parsed.filters.days or 60,
        limit=40,
    )
    rows = (result.data or {}).get("incidents") or []
    candidates = [
        SearchHit(
            domain="incident",
            id=str(r.get("id") or ""),
            title=str(r.get("title") or r.get("incident_type") or "incident"),
            snippet=str(r.get("description") or "")[:240],
            score=0.0,
            source="structured",
            metadata=r if isinstance(r, dict) else {},
        )
        for r in rows
        if isinstance(r, dict)
    ]
    # Enrich description if missing — get detail for top structured keyword miss
    if not any(conceptual_score(c.snippet + c.title, parsed.filters.conceptual_terms) > 0.1 for c in candidates):
        # retry with freezer/q needle via structured
        needle = parsed.filters.q or " ".join(parsed.filters.conceptual_terms[:3])
        result2 = find_incidents(ctx, q=needle, status="ALL", days=parsed.filters.days or 60, limit=20)
        for r in (result2.data or {}).get("incidents") or []:
            if not isinstance(r, dict):
                continue
            candidates.append(
                SearchHit(
                    domain="incident",
                    id=str(r.get("id") or ""),
                    title=str(r.get("title") or ""),
                    snippet=str(r.get("description") or "")[:240],
                    score=0.0,
                    source="structured",
                    metadata=r,
                )
            )
    return semantic_rerank(ctx, parsed, candidates)
