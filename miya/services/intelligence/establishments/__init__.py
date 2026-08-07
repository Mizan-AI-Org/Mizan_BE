"""
Phase 8 — Multi-Establishment Intelligence.

Hierarchy: Organization → Establishment → Department → User → Role → Permissions.

Rules:
  - One establishment visible → answer directly (auto-bound).
  - Multiple + no active context → ask which establishment.
  - "What about Casablanca?" → switch sticky context.
  - Never leak tasks/staff/incidents/invoices/documents across establishments.
"""
from __future__ import annotations

from miya.services.intelligence.establishments.gate import (
    deny_cross_establishment_entity,
    deny_inaccessible_establishment,
    ensure_establishment_for_ops,
    looks_like_establishment_switch,
    scope_snapshot,
    try_establishment_switch,
)
from miya.services.intelligence.establishments.hierarchy import (
    EstablishmentScope,
    build_establishment_scope,
    clarify_which_establishment,
)

__all__ = [
    "EstablishmentScope",
    "build_establishment_scope",
    "clarify_which_establishment",
    "deny_cross_establishment_entity",
    "deny_inaccessible_establishment",
    "ensure_establishment_for_ops",
    "looks_like_establishment_switch",
    "scope_snapshot",
    "try_establishment_switch",
]
