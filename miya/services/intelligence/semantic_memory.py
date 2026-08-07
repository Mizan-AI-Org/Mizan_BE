"""Semantic Memory — optional historical conversation recall.

Mastra keeps semanticRecall OFF by default (Observational Memory preferred).
When enabled, recall is advisory only and MUST NOT override database state.
"""
from __future__ import annotations

import os
from typing import Any


def semantic_recall_enabled() -> bool:
    raw = (os.environ.get("MIYA_SEMANTIC_RECALL_ENABLED") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def load_semantic_memory(
    *,
    query: str = "",
    hits: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Package semantic hits (if any) with hard safety rails.
    Callers must already have filtered to relevant, non-status claims.
    """
    enabled = semantic_recall_enabled()
    safe_hits = []
    for h in hits or []:
        if not isinstance(h, dict):
            continue
        # Drop hits that look like status assertions — DB must answer those
        text = str(h.get("text") or h.get("content") or "").lower()
        if any(
            token in text
            for token in (
                "completed",
                "resolved",
                "approved",
                "paid",
                "status is",
                "marked as",
            )
        ):
            continue
        safe_hits.append(h)
    return {
        "layer": "SEMANTIC_HISTORICAL_RECALL",
        "enabled": enabled,
        "query": (query or "")[:200] or None,
        "hits": safe_hits if enabled else [],
        "authority": "lowest",
        "directive": (
            "Semantic recall is optional and lowest priority. "
            "Never use it for current status. Prefer get_current_* and operational events. "
            "Do not retrieve arbitrary similar messages — only when clearly relevant to intent."
        ),
    }
