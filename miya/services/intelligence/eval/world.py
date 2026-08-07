"""Deterministic world fixtures for eval simulation."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from miya.services.intelligence.eval.types import EvalCase, WorldEntity
from miya.services.ops.context import OpsContext
from miya.services.ops.result import clarify, fail, ok


def build_ops_context(case: EvalCase) -> OpsContext:
    """Build OpsContext from an eval case."""
    user = MagicMock()
    user.id = case.session.get("user_id", "eval-user")
    user.pk = user.id
    user.role = case.role

    rest = MagicMock()
    rest.id = case.session.get("restaurant_id", "eval-org")
    rest.name = case.session.get("restaurant_name", "Mizan Eval Org")

    locs = case.session.get("available_locations") or [
        {"id": "loc-casa", "name": "Casablanca"},
        {"id": "loc-rabat", "name": "Rabat"},
    ]
    location_id = case.session.get("location_id")
    location_name = case.session.get("location_name")
    if location_id and not location_name:
        location_name = next(
            (L["name"] for L in locs if L.get("id") == location_id),
            None,
        )

    return OpsContext(
        user=user,
        restaurant=rest,
        restaurant_id=str(rest.id),
        user_id=str(user.id),
        role=case.role,
        channel=case.channel,
        language=case.session.get("language", "en"),
        location_id=location_id,
        location_name=location_name,
        available_locations=locs,
    )


def _entities(case: EvalCase, kind: str) -> list[WorldEntity]:
    return [e for e in case.world if e.kind == kind]


def _match_query(entity: WorldEntity, q: str) -> bool:
    needle = (q or "").strip().lower()
    if not needle:
        return False
    hay = f"{entity.title} {entity.id} {entity.extra.get('ref', '')}".lower()
    return needle in hay or hay in needle


def mock_get_task_state(case: EvalCase, *, q: str = "", title: str = "", **_: Any):
    tasks = _entities(case, "task")
    needle = (q or title or "").strip()
    matches = [t for t in tasks if _match_query(t, needle)]
    if len(matches) == 1:
        t = matches[0]
        return ok(
            message="found",
            verified=True,
            data={
                "task": {
                    "id": t.id,
                    "title": t.title,
                    "status": t.status,
                    "location_id": t.location_id,
                }
            },
        )
    if len(matches) > 1:
        return clarify(
            message="Several tasks match",
            data={
                "candidates": [
                    {"id": t.id, "title": t.title, "status": t.status}
                    for t in matches
                ]
            },
        )
    return fail(code="task_not_found", message=f"No task matching '{needle}'.")


def mock_find_tasks(case: EvalCase, *, q: str = "", limit: int = 5, **_: Any):
    tasks = _entities(case, "task")
    needle = (q or "").strip()
    matches = [t for t in tasks if _match_query(t, needle)] if needle else tasks
    return ok(
        message="listed",
        verified=True,
        data={
            "tasks": [
                {"id": t.id, "title": t.title, "status": t.status}
                for t in matches[:limit]
            ],
            "count": len(matches),
        },
    )


def mock_get_incident(case: EvalCase, *, q: str = "", **_: Any):
    incidents = _entities(case, "incident")
    needle = (q or "").strip()
    matches = [i for i in incidents if _match_query(i, needle)]
    if len(matches) == 1:
        i = matches[0]
        return ok(
            message="found",
            verified=True,
            data={
                "incident": {
                    "id": i.id,
                    "title": i.title,
                    "status": i.status,
                    "location_id": i.location_id,
                }
            },
        )
    if len(matches) > 1:
        return clarify(
            message="Several incidents match",
            data={
                "candidates": [
                    {"id": i.id, "title": i.title, "status": i.status}
                    for i in matches
                ]
            },
        )
    return fail(code="incident_not_found", message=f"No incident matching '{needle}'.")


def mock_execute_structured_action(
    case: EvalCase,
    name: str,
    arguments: dict[str, Any] | None,
    *,
    ctx: OpsContext | None = None,
    execution_context: dict[str, Any] | None = None,
    intent: str = "",
):
    """Simulate mutation success from expected db_state."""
    exp = case.expected
    if exp.permission_allowed is False:
        return fail(
            code="permission_denied",
            message="You don't have permission to do that in this workspace.",
        )

    db_after = dict(exp.db_state or {})
    entity_id = (arguments or {}).get("task_id") or (arguments or {}).get("incident_id") or ""
    payload: dict[str, Any] = {}
    if "status" in db_after:
        payload["task"] = {
            "id": entity_id or "sim-entity",
            "status": db_after["status"],
        }
    if "incident_status" in db_after:
        payload["incident"] = {
            "id": entity_id or "sim-entity",
            "status": db_after["incident_status"],
        }

    return ok(
        message=case.expected.response_must_contain[0]
        if case.expected.response_must_contain
        else "Done.",
        verified=True if exp.verified is not False else False,
        data=payload,
    )


WORKFLOW_TO_TOOL: dict[str, str] = {
    "task_completion": "complete_task",
    "task_assignment": "assign_task",
    "incident_routing": "create_incident",
    "invoice_from_media": "record_invoice",
    "document_processing": "retrieve_document",
    "incident_lookup": "get_current_incident",
    "staff_lookup": "find_staff",
    "invoice_approval": "approve_invoice",
    "reminder_creation": "create_reminder",
    "meeting_creation": "create_meeting",
    "compliance_reminder_from_media": "sync_compliance_reminder",
}
