"""Turn-level establishment gate: clarify, switch, never leak."""
from __future__ import annotations

import re
from typing import Any

from miya.services.intelligence.establishments.hierarchy import (
    build_establishment_scope,
    clarify_which_establishment,
)
from miya.services.ops.context import OpsContext, require_establishment_context
from miya.services.ops.result import OpsResult, fail, ok

_SWITCH = re.compile(
    r"^\s*(?:what\s+about|how\s+about|switch\s+to|go\s+to|use|pour)\s+"
    r"(.+?)\s*\??\s*$",
    re.I,
)
_OPS_QUERY = re.compile(
    r"\b(incident|task|invoice|staff|document|checklist|today|pending|overdue|"
    r"meeting|what\s+are|show|list|find)\b",
    re.I,
)


def looks_like_establishment_switch(message: str) -> str | None:
    """Return establishment name needle if message is a context switch."""
    m = _SWITCH.match((message or "").strip())
    if not m:
        return None
    name = m.group(1).strip(" .!?")
    # Avoid treating ops questions as switches: "What about the freezer incident?"
    if re.search(
        r"\b(incident|task|invoice|checklist|document|delivery|freezer|staff)\b",
        name,
        re.I,
    ):
        return None
    if len(name) < 2:
        return None
    return name


def try_establishment_switch(
    ctx: OpsContext,
    message: str,
) -> OpsResult | None:
    """
    'What about Casablanca?' → set_establishment_context.
    Returns OpsResult if handled, else None.
    """
    needle = looks_like_establishment_switch(message)
    if not needle:
        return None
    from miya.services.ops.establishments import set_establishment_context

    return set_establishment_context(ctx, q=needle)


def ensure_establishment_for_ops(
    ctx: OpsContext,
    *,
    for_action: str = "this",
    message: str = "",
) -> OpsResult | None:
    """
    Single establishment → OK (auto-bound by OpsContext).
    Multi + no active → clarify "Which establishment do you mean?"
    Unless message already names one (handled by switch) or context is set.
    """
    scope = build_establishment_scope(ctx)
    if not scope.needs_establishment_choice:
        return None
    if message and not _OPS_QUERY.search(message):
        # Non-ops chit-chat — don't force clarify
        return None
    err = require_establishment_context(ctx, for_action=for_action)
    if err:
        # Normalize copy to product example phrasing when possible
        if err.code == "needs_establishment":
            err.message_for_user = clarify_which_establishment(scope, for_action=for_action)
        return err
    return None


def deny_inaccessible_establishment(
    ctx: OpsContext,
    location_id: str,
) -> OpsResult:
    """Hard fail for unauthorized establishment — used by security tests & guards."""
    from miya.services.ops.context import assert_location_access

    denied = assert_location_access(ctx, location_id)
    if denied:
        return denied
    return fail(
        code="location_forbidden",
        message="You don't have access to that establishment.",
        miya_directive="Do not reveal data from establishments the user cannot access.",
    )


def deny_cross_establishment_entity(
    ctx: OpsContext,
    *,
    entity_location_id: str | None,
    entity_type: str = "record",
) -> OpsResult | None:
    """
    Fail closed if entity belongs to another establishment or inaccessible branch.
    Never return the foreign entity's payload.
    """
    from miya.services.ops.context import guard_entity_location

    if not entity_location_id:
        return None
    entity = type("E", (), {"location_id": entity_location_id, "business_location_id": None})()
    return guard_entity_location(ctx, entity)


def scope_snapshot(ctx: OpsContext) -> dict[str, Any]:
    return build_establishment_scope(ctx).to_dict()
