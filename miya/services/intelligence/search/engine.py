"""Phase 7 orchestrator — structured + semantic + metadata + events."""
from __future__ import annotations

import logging
from typing import Any

from miya.services.intelligence.search.classify_query import parse_search_query
from miya.services.intelligence.search.events import event_search
from miya.services.intelligence.search.semantic import conceptual_incident_search, semantic_rerank
from miya.services.intelligence.search.structured import structured_search
from miya.services.intelligence.search.types import (
    ParsedSearchQuery,
    SearchDomain,
    SearchMode,
    SearchResult,
)
from miya.services.ops import build_ops_context
from miya.services.ops.context import OpsContext

logger = logging.getLogger("miya.intelligence.search")


def operational_search(
    *,
    user,
    query: str,
    restaurant=None,
    session_context: dict[str, Any] | None = None,
    channel: str = "dashboard",
) -> SearchResult:
    """
    Natural-language operational search.

    Strategy:
      STRUCTURED → find_* only
      SEMANTIC   → conceptual rank over scoped candidates (no vector-for-everything)
      HYBRID     → structured filter + conceptual rank
      EVENT      → event history / operational memory (+ hybrid if needed)

    Every path uses OpsContext (org, establishment, permissions).
    """
    ctx_dict = dict(session_context or {})
    ctx_dict.setdefault("channel", channel)
    if restaurant is None:
        restaurant = getattr(user, "restaurant", None)
    ops = build_ops_context(user=user, restaurant=restaurant, session_context=ctx_dict)
    if ops is None:
        parsed = parse_search_query(query, session_context=ctx_dict)
        return SearchResult(
            query=parsed,
            hits=[],
            reply="I need your workspace to search.",
            success=False,
            scoped={"error": "no_workspace"},
            strategy=["denied"],
        )

    # Phase 8: never search across establishments when multi + unset
    try:
        from miya.services.intelligence.establishments import ensure_establishment_for_ops

        gate = ensure_establishment_for_ops(
            ops, for_action="this search", message=query or ""
        )
        if gate is not None and gate.needs_clarification:
            parsed = parse_search_query(query, session_context=ctx_dict)
            return SearchResult(
                query=parsed,
                hits=[],
                reply=gate.message_for_user,
                success=False,
                scoped=_scope_meta(ops),
                strategy=["needs_establishment"],
            )
    except Exception:
        logger.exception("establishment gate in search failed")

    parsed = parse_search_query(query, session_context={
        **ctx_dict,
        "restaurant_id": ops.restaurant_id,
        "location_id": ops.location_id or "",
    })
    return execute_search(ops, parsed)


def execute_search(ctx: OpsContext, parsed: ParsedSearchQuery) -> SearchResult:
    strategy: list[str] = [f"mode:{parsed.mode.value}", f"domain:{parsed.domain.value}"]
    hits = []

    try:
        if parsed.mode == SearchMode.STRUCTURED:
            hits, st = structured_search(ctx, parsed)
            strategy.extend(st)

        elif parsed.mode == SearchMode.EVENT:
            hits = event_search(ctx, parsed)
            strategy.append("event_history")
            if not hits and parsed.domain in (SearchDomain.INCIDENT, SearchDomain.MIXED):
                hits = conceptual_incident_search(ctx, parsed)
                strategy.append("event_fallback_hybrid_incident")

        elif parsed.mode == SearchMode.SEMANTIC:
            if parsed.domain == SearchDomain.INCIDENT:
                hits = conceptual_incident_search(ctx, parsed)
                strategy.append("conceptual_incident")
            else:
                candidates, st = structured_search(ctx, parsed)
                strategy.extend(st)
                hits = semantic_rerank(ctx, parsed, candidates)
                strategy.append("semantic_rerank")

        else:  # HYBRID
            if parsed.domain == SearchDomain.INCIDENT or "complain" in parsed.raw.lower():
                hits = conceptual_incident_search(ctx, parsed)
                strategy.append("hybrid_conceptual_incident")
            else:
                candidates, st = structured_search(ctx, parsed)
                strategy.extend(st)
                hits = semantic_rerank(ctx, parsed, candidates)
                strategy.append("hybrid_rerank")
            # Merge event hints when asking "what happened"
            if "happen" in parsed.raw.lower() or "handled" in parsed.raw.lower():
                ev = event_search(ctx, parsed)
                strategy.append("hybrid_events")
                hits = _merge(hits, ev)

    except Exception:
        logger.exception("operational_search failed")
        return SearchResult(
            query=parsed,
            hits=[],
            reply="Something went wrong while searching.",
            success=False,
            scoped=_scope_meta(ctx),
            strategy=strategy + ["error"],
        )

    hits = hits[:25]
    reply = format_search_reply(parsed, hits)
    return SearchResult(
        query=parsed,
        hits=hits,
        reply=reply,
        success=True,
        scoped=_scope_meta(ctx),
        strategy=strategy,
    )


def format_search_reply(parsed: ParsedSearchQuery, hits: list) -> str:
    if not hits:
        return (
            "I couldn't find anything matching that in your workspace "
            "(respecting establishment and permissions). "
            "Try a name, vendor, or shorter phrase."
        )
    lines = [f"Found {len(hits)} result{'s' if len(hits) != 1 else ''} ({parsed.mode.value.lower()} search):", ""]
    for i, h in enumerate(hits[:8], 1):
        bit = f"{i}. [{h.domain}] {h.title}"
        if h.snippet:
            bit += f" — {h.snippet[:100]}"
        lines.append(bit)
    if len(hits) > 8:
        lines.append(f"…and {len(hits) - 8} more.")
    return "\n".join(lines)


def _scope_meta(ctx: OpsContext) -> dict[str, Any]:
    return {
        "organization_id": ctx.restaurant_id,
        "establishment_id": ctx.location_id,
        "user_id": ctx.user_id,
        "role": ctx.role,
        "channel": ctx.channel,
        "available_locations": len(ctx.available_locations or []),
    }


def _merge(a, b):
    seen = {f"{h.domain}:{h.id}" for h in a}
    out = list(a)
    for h in b:
        key = f"{h.domain}:{h.id}"
        if key not in seen:
            seen.add(key)
            out.append(h)
    out.sort(key=lambda x: -x.score)
    return out
