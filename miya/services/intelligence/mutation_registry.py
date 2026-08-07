"""
Phase 11 — inventory of Miya mutation tools and canonical migration targets.

Every state-changing tool must either:
  1. Route through ``execute_structured_action`` / ``dispatch_canonical_tool`` with DB verify, or
  2. Fail closed at the legacy HTTP boundary (``legacy_unverified_mutation``).
"""
from __future__ import annotations

from typing import Any

from miya.services.intelligence.actions import ACTION_CATALOG, resolve_action_name
from miya.services.ops import CANONICAL_TOOL_NAMES

# POST tools that only read (safe to allow through legacy HTTP without verified gate).
_READ_POST_TOOLS = frozenset(
    {
        "list_dashboard_widgets",
        "list_dashboard_tasks",
        "get_dashboard_task",
        "list_operations_live",
        "list_invoices",
        "list_automations",
        "get_invoice_timeline",
        "platform_knowledge",
        "staff_lookup",
    }
)

# Deferred — OCR / admin migrations tracked in later phases.
_DEFERRED_MUTATION_TOOLS = frozenset(
    {
        "parse_photo",
        "parse_document",
        "create_automation",
        "create_custom_widget",
        "dashboard_widgets_add",
        "seed_compliance_documents",
    }
)

# Explicit map: legacy tool name → canonical structured action (when not an alias).
MIGRATION_MAP: dict[str, str] = {
    "update_dashboard_task": "update_task",
    "assign_invoice": "assign_invoice",  # canonical dispatch TBD — still legacy until ops handler exists
    "staff_clock_in": "staff_clock_in",
    "staff_clock_out": "staff_clock_out",
    "staff_request": "staff_request",
    "approve_staff_request": "approve_staff_request",
    "reject_staff_request": "reject_staff_request",
    "request_time_off": "request_time_off",
    "create_shift": "create_shift",
    "assign_coverage": "assign_coverage",
    "mark_no_show": "mark_no_show",
    "notify_manager_urgent": "notify_manager_urgent",
    "send_announcement": "send_announcement",
    "chase_operational_record": "chase_operational_record",
    "report_waste": "report_waste",
    "update_compliance_document": "update_compliance_document",
    "recognize_staff": "recognize_staff",
}


def _route_map() -> dict[str, tuple[str, str]]:
    from miya.services.tools import _ROUTE_MAP

    return _ROUTE_MAP


def _mutating_http_methods() -> frozenset[str]:
    return frozenset({"POST", "PUT", "PATCH", "DELETE"})


def is_read_post_tool(name: str) -> bool:
    return (name or "").strip() in _READ_POST_TOOLS


def is_deferred_mutation_tool(name: str) -> bool:
    return (name or "").strip() in _DEFERRED_MUTATION_TOOLS


def is_structured_mutation(name: str) -> bool:
    """True when tool resolves to a structured action with mutates=True."""
    action = resolve_action_name(name)
    return bool((ACTION_CATALOG.get(action) or {}).get("mutates"))


def is_canonical_mutation(name: str) -> bool:
    """Mutation tool routed via dispatch_canonical_tool (may still need verify gate)."""
    tool = (name or "").strip()
    if tool not in CANONICAL_TOOL_NAMES:
        return False
    route = _route_map().get(tool)
    if route and route[0] not in _mutating_http_methods():
        return False
    if is_read_post_tool(tool):
        return False
    return True


def is_legacy_http_mutation(name: str) -> bool:
    """Mutation that would hit legacy HTTP if canonical/structured dispatch misses."""
    tool = (name or "").strip()
    if is_read_post_tool(tool) or is_deferred_mutation_tool(tool):
        return False
    if is_structured_mutation(tool) or is_canonical_mutation(tool):
        return False
    route = _route_map().get(tool)
    if not route:
        return is_structured_mutation(tool)
    return route[0] in _mutating_http_methods()


def is_mutation_tool(name: str) -> bool:
    return is_structured_mutation(name) or is_canonical_mutation(name) or is_legacy_http_mutation(name)


def canonical_target(name: str) -> str | None:
    """Best canonical action/tool for a legacy mutation."""
    tool = (name or "").strip()
    if tool in MIGRATION_MAP:
        return MIGRATION_MAP[tool]
    action = resolve_action_name(tool)
    if action != tool and (ACTION_CATALOG.get(action) or {}).get("mutates"):
        return action
    if tool in CANONICAL_TOOL_NAMES:
        return tool
    return MIGRATION_MAP.get(tool)


def inventory_mutations() -> dict[str, Any]:
    """Full Phase 11 inventory for reports and tests."""
    routes = _route_map()
    structured: list[str] = []
    canonical: list[str] = []
    legacy: list[str] = []
    deferred: list[str] = []
    reads: list[str] = []

    for tool, (method, _path) in sorted(routes.items()):
        if is_read_post_tool(tool):
            reads.append(tool)
            continue
        if is_deferred_mutation_tool(tool):
            deferred.append(tool)
            continue
        if method not in _mutating_http_methods():
            continue
        if is_structured_mutation(tool):
            structured.append(tool)
        elif tool in CANONICAL_TOOL_NAMES:
            canonical.append(tool)
        else:
            legacy.append(tool)

    return {
        "structured_spine": sorted(set(structured)),
        "canonical_dispatch": sorted(set(canonical)),
        "legacy_http_blocked": sorted(set(legacy)),
        "deferred": sorted(deferred),
        "read_post_exempt": sorted(reads),
        "migration_map": dict(MIGRATION_MAP),
    }
