"""Event History — query the durable OperationalEvent stream."""
from __future__ import annotations

from miya.services.intelligence.operational_memory import list_operational_events
from miya.services.ops.context import OpsContext
from miya.services.ops.result import OpsResult, ok


def get_event_history(
    ctx: OpsContext,
    *,
    event_type: str = "",
    entity_type: str = "",
    entity_id: str = "",
    q: str = "",
    limit: int = 40,
) -> OpsResult:
    result = list_operational_events(
        ctx,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        q=q,
        limit=limit,
    )
    if not result.success:
        return result
    data = dict(result.data or {})
    data["layer"] = "RECENT_OPERATIONAL_EVENT"
    return ok(
        message=result.message_for_user,
        verified=result.verified,
        data=data,
        miya_directive=result.miya_directive,
    )
