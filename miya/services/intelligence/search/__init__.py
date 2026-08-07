"""
Phase 7 — Semantic Operational Search.

STRUCTURED DB + SEMANTIC/conceptual rank + METADATA filters + EVENT HISTORY.
Never vector-search everything. Always scoped by org / establishment / permissions.
"""
from __future__ import annotations

from miya.services.intelligence.search.classify_query import parse_search_query
from miya.services.intelligence.search.engine import execute_search, operational_search
from miya.services.intelligence.search.types import SearchMode, SearchDomain

__all__ = [
    "SearchDomain",
    "SearchMode",
    "execute_search",
    "operational_search",
    "parse_search_query",
]
