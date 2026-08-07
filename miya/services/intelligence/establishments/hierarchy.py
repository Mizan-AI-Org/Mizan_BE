"""Organization → Establishment → Department → User → Role → Permissions scope."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from miya.services.ops.context import OpsContext


@dataclass
class EstablishmentScope:
    """Canonical multi-establishment identity for a Miya turn."""

    organization_id: str
    organization_name: str = ""
    establishment_id: str | None = None
    establishment_name: str | None = None
    department: str | None = None
    user_id: str = ""
    role: str = ""
    permissions_note: str = "actions gated via require_permission / RBAC"
    available_establishments: list[dict[str, Any]] = field(default_factory=list)
    is_multi_establishment: bool = False
    needs_establishment_choice: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_establishment_scope(ctx: OpsContext, *, department: str | None = None) -> EstablishmentScope:
    locs = list(ctx.available_locations or [])
    multi = len(locs) > 1
    active = bool(ctx.location_id)
    return EstablishmentScope(
        organization_id=str(ctx.restaurant_id or ""),
        organization_name=str(getattr(ctx.restaurant, "name", "") or ""),
        establishment_id=str(ctx.location_id) if ctx.location_id else None,
        establishment_name=ctx.location_name,
        department=(department or "").strip() or None,
        user_id=str(ctx.user_id or ""),
        role=str(ctx.role or ""),
        available_establishments=locs,
        is_multi_establishment=multi,
        needs_establishment_choice=multi and not active,
    )


def clarify_which_establishment(scope: EstablishmentScope, *, for_action: str = "this") -> str:
    names = ", ".join(
        str(r.get("name") or "") for r in (scope.available_establishments or [])[:8] if r.get("name")
    )
    return (
        f"Which establishment do you mean for {for_action}? "
        f"You have access to: {names}."
    )
