"""Working Memory — current focus pointers (not status).

Durable via WorkingMemorySnapshot (DB). Survives server restart.
Status of pointed entities must always be re-fetched from the database.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("miya.intelligence.working_memory")


def get_working_memory(*, user, restaurant) -> dict[str, Any]:
    if not user or not restaurant or not getattr(user, "pk", None):
        return {"layer": "WORKING_MEMORY", "empty": True}
    try:
        from miya.models import WorkingMemorySnapshot

        snap = WorkingMemorySnapshot.objects.filter(
            restaurant=restaurant, user=user
        ).first()
        if not snap:
            return {
                "layer": "WORKING_MEMORY",
                "empty": True,
                "authority": "working_memory_pointers_only",
            }
        data = snap.as_dict()
        data["layer"] = "WORKING_MEMORY"
        data["empty"] = False
        return data
    except Exception:
        logger.exception("get_working_memory failed")
        return {"layer": "WORKING_MEMORY", "empty": True, "error": True}


def update_working_memory(
    *,
    user,
    restaurant,
    establishment_id: str | None = None,
    establishment_name: str | None = None,
    department: str | None = None,
    current_task_id: str | None = None,
    current_task_label: str | None = None,
    current_incident_id: str | None = None,
    current_incident_label: str | None = None,
    current_document_id: str | None = None,
    current_document_label: str | None = None,
    current_invoice_id: str | None = None,
    current_invoice_label: str | None = None,
    current_workflow: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not user or not restaurant or not getattr(user, "pk", None):
        return {"ok": False, "reason": "missing_user_or_org"}
    # Skip non-persistent / mock restaurants (unit tests)
    try:
        import uuid as _uuid

        _uuid.UUID(str(getattr(restaurant, "id", "") or ""))
        _uuid.UUID(str(getattr(user, "id", getattr(user, "pk", "")) or ""))
    except Exception:
        return {"ok": False, "reason": "invalid_ids"}
    try:
        from miya.models import WorkingMemorySnapshot

        snap, _ = WorkingMemorySnapshot.objects.get_or_create(
            restaurant=restaurant,
            user=user,
        )
        fields: list[str] = []
        mapping = {
            "establishment_id": establishment_id,
            "establishment_name": establishment_name,
            "department": department,
            "current_task_id": current_task_id,
            "current_task_label": current_task_label,
            "current_incident_id": current_incident_id,
            "current_incident_label": current_incident_label,
            "current_document_id": current_document_id,
            "current_document_label": current_document_label,
            "current_invoice_id": current_invoice_id,
            "current_invoice_label": current_invoice_label,
            "current_workflow": current_workflow,
        }
        for attr, value in mapping.items():
            if value is not None:
                setattr(snap, attr, str(value)[:255] if attr.endswith("_label") or attr in (
                    "establishment_name",
                    "department",
                    "current_workflow",
                ) else str(value)[:64])
                fields.append(attr)
        if extra:
            merged = dict(snap.extra or {})
            merged.update(extra)
            snap.extra = merged
            fields.append("extra")
        if fields:
            snap.save(update_fields=[*fields, "updated_at"])
        return {"ok": True, "snapshot": snap.as_dict()}
    except Exception:
        logger.exception("update_working_memory failed")
        return {"ok": False, "reason": "persist_failed"}


def touch_from_entity(
    *,
    user,
    restaurant,
    entity_type: str,
    entity_id: str,
    entity_label: str = "",
    establishment_id: str = "",
    establishment_name: str = "",
    workflow: str = "",
) -> None:
    """Update the relevant current_* pointer after a mutation or retrieve."""
    et = (entity_type or "").lower()
    kwargs: dict[str, Any] = {}
    if establishment_id:
        kwargs["establishment_id"] = establishment_id
    if establishment_name:
        kwargs["establishment_name"] = establishment_name
    if workflow:
        kwargs["current_workflow"] = workflow
    if et == "task":
        kwargs["current_task_id"] = entity_id
        kwargs["current_task_label"] = entity_label
    elif et == "incident":
        kwargs["current_incident_id"] = entity_id
        kwargs["current_incident_label"] = entity_label
    elif et == "document":
        kwargs["current_document_id"] = entity_id
        kwargs["current_document_label"] = entity_label
    elif et == "invoice":
        kwargs["current_invoice_id"] = entity_id
        kwargs["current_invoice_label"] = entity_label
    if kwargs:
        update_working_memory(user=user, restaurant=restaurant, **kwargs)
