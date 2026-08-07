"""Canonical process template import — authorize, mutate, verify, audit."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from django.core.cache import cache

from core.operational_audit.service import (
    TASK_TEMPLATE_CREATED,
    TASK_TEMPLATE_IMPORTED,
    record_operational_audit_event,
)
from miya.services.ops.context import OpsContext, require_permission, require_restaurant
from miya.services.ops.result import OpsResult, fail, ok

logger = logging.getLogger(__name__)


def _serialize_template(row) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "template_type": row.template_type,
        "tasks_count": len(row.tasks or []),
    }


def _operation_key(operation_id: str, *, prefix: str = "process-import") -> str:
    op = (operation_id or "").strip() or str(uuid.uuid4())
    return f"{prefix}:{op}"


def _import_operation_key(operation_id: str) -> str:
    return _operation_key(operation_id, prefix="process-import")


def _create_operation_key(operation_id: str) -> str:
    return _operation_key(operation_id, prefix="task-template-create")


def _normalize_task_steps(tasks_raw: list[Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for item in tasks_raw or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        priority = (str(item.get("priority") or "MEDIUM")).upper()[:20] or "MEDIUM"
        steps.append(
            {
                "id": str(uuid.uuid4()),
                "title": title,
                "description": str(item.get("description") or "").strip(),
                "priority": priority,
                "completed": False,
            }
        )
    return steps


def _infer_template_type(name: str, template_type: str = "") -> str:
    from scheduling.task_templates import TaskTemplate

    valid_types = {c[0] for c in TaskTemplate.TEMPLATE_TYPES}
    t = (template_type or "").upper().strip()
    if t in valid_types:
        return t
    n = (name or "").lower()
    if any(k in n for k in ("opening", "open checklist", "mise en place", "démarrage", "ouverture")):
        return "OPENING"
    if any(k in n for k in ("closing", "close checklist", "fermeture", "close-down")):
        return "CLOSING"
    if any(k in n for k in ("clean", "hygiene", "nettoyage")):
        return "CLEANING"
    if any(k in n for k in ("maintenance", "equipment", "entretien")):
        return "MAINTENANCE"
    if any(k in n for k in ("safety", "haccp", "health", "sécurité")):
        return "SAFETY"
    return "CUSTOM"


def _replay_idempotent_create(ctx: OpsContext, operation_key: str) -> OpsResult | None:
    from miya.models import OperationalEvent
    from scheduling.task_templates import TaskTemplate

    event = (
        OperationalEvent.objects.filter(
            restaurant=ctx.restaurant,
            event_type=TASK_TEMPLATE_CREATED,
            operation_id__startswith=operation_key,
        )
        .order_by("created_at")
        .first()
    )
    if not event or not event.entity_id:
        return None
    row = TaskTemplate.objects.filter(
        id=event.entity_id,
        restaurant=ctx.restaurant,
        is_active=True,
    ).first()
    if not row:
        return None
    serialized = _serialize_template(row)
    return ok(
        message=(
            f"Created template '{row.name}' with {serialized['tasks_count']} task(s) (already applied)."
        ),
        verified=True,
        data={
            "task_template": serialized,
            "operation": "create_task_template",
            "operation_id": operation_key,
            "deduplicated": True,
            "audit_emitted": True,
        },
    )


def _emit_template_create_audit(ctx: OpsContext, *, template, operation_key: str) -> None:
    record_operational_audit_event(
        restaurant=ctx.restaurant,
        event_type=TASK_TEMPLATE_CREATED,
        entity_type="task_template",
        entity_id=str(template.id),
        entity_label=template.name or "",
        actor=ctx.user,
        location_id=ctx.location_id or "",
        channel=ctx.channel or "agent",
        operation_id=f"{operation_key}:{template.id}",
        idempotency_key=f"{operation_key}:{template.id}",
        new_state=_serialize_template(template),
        summary=f"Process template created: {template.name}",
    )


def _authorize_template_mutation(ctx: OpsContext, *, action: str) -> OpsResult | None:
    if not ctx.user or not getattr(ctx.user, "pk", None):
        return fail(
            code="actor_required",
            message=f"An authenticated user is required to {action}.",
        )
    err = require_restaurant(ctx)
    if err:
        return err
    return require_permission(ctx, "manage_widgets")


def create_task_template(
    ctx: OpsContext,
    *,
    name: str,
    tasks: list[Any],
    description: str = "",
    template_type: str = "",
    ai_prompt: str = "",
    operation_id: str = "",
) -> OpsResult:
    """Canonical single-template create: authorize → mutate → verify → audit."""
    auth_err = _authorize_template_mutation(ctx, action="create process templates")
    if auth_err:
        return auth_err

    clean_name = (name or "").strip()
    if not clean_name:
        return fail(code="missing_name", message="Template name is required.")

    normalized_tasks = _normalize_task_steps(tasks)
    if not normalized_tasks:
        return fail(
            code="missing_tasks",
            message="At least one task step with a title is required.",
        )

    operation_key = _create_operation_key(operation_id)
    replay = _replay_idempotent_create(ctx, operation_key)
    if replay is not None:
        return replay

    try:
        from miya.services.message_pipeline import claim_mutation_once

        if not claim_mutation_once(operation_key, ttl_seconds=300):
            replay = _replay_idempotent_create(ctx, operation_key)
            if replay is not None:
                return replay
            return ok(
                message="That template creation is already in progress or was just applied.",
                verified=True,
                code="duplicate_suppressed",
                data={"operation": "create_task_template", "deduplicated": True},
            )
    except Exception:
        logger.exception("task template create idempotency claim failed")

    from scheduling.task_templates import TaskTemplate

    if TaskTemplate.objects.filter(
        restaurant=ctx.restaurant, name=clean_name, is_active=True
    ).exists():
        return fail(
            code="duplicate_name",
            message=f"A template named '{clean_name}' already exists in this workspace.",
        )

    ttype = _infer_template_type(clean_name, template_type)
    row = TaskTemplate.objects.create(
        restaurant=ctx.restaurant,
        name=clean_name[:255],
        description=(description or "").strip() or None,
        template_type=ttype,
        tasks=normalized_tasks,
        frequency="CUSTOM",
        ai_generated=True,
        ai_prompt=(ai_prompt or f"Created for shift: {clean_name}")[:500],
        created_by=ctx.user,
        is_active=True,
    )

    expected = {
        "id": str(row.id),
        "name": clean_name,
        "tasks_count": len(normalized_tasks),
    }
    verified_rows, verr = _verify_created_templates(ctx, expected=[expected])
    if verr is not None:
        return verr

    verified = verified_rows[0]
    _emit_template_create_audit(ctx, template=verified, operation_key=operation_key)

    try:
        cache.delete(f"agent:sched:task_templates:{ctx.restaurant.id}")
    except Exception:
        pass

    serialized = _serialize_template(verified)
    return ok(
        message=(
            f"Created template '{verified.name}' with {serialized['tasks_count']} task(s). "
            f"It appears under Processes & Tasks → Templates on the dashboard."
        ),
        verified=True,
        data={
            "task_template": serialized,
            "operation": "create_task_template",
            "operation_id": operation_key,
            "audit_emitted": True,
        },
    )


def _replay_idempotent_import(ctx: OpsContext, operation_key: str) -> OpsResult | None:
    """Return verified replay when this operation_id already completed."""
    from miya.models import OperationalEvent
    from scheduling.task_templates import TaskTemplate

    events = list(
        OperationalEvent.objects.filter(
            restaurant=ctx.restaurant,
            event_type=TASK_TEMPLATE_IMPORTED,
            operation_id__startswith=operation_key,
        ).order_by("created_at")
    )
    if not events:
        return None

    created_ids = [str(e.entity_id) for e in events if e.entity_id]
    if not created_ids:
        return None

    rows = list(
        TaskTemplate.objects.filter(
            restaurant=ctx.restaurant,
            id__in=created_ids,
            is_active=True,
        )
    )
    if len(rows) != len(created_ids):
        return None

    created = [_serialize_template(r) for r in rows]
    skipped: list[dict[str, Any]] = []
    for ev in events:
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        skipped = payload.get("skipped") or skipped

    names = ", ".join(c["name"] for c in created[:5])
    extra = f" (+{len(created) - 5} more)" if len(created) > 5 else ""
    return ok(
        message=f"Imported {len(created)} process(es) (already applied): {names}{extra}.",
        verified=True,
        data={
            "created": created,
            "skipped": skipped,
            "operation": "import_process_templates",
            "operation_id": operation_key,
            "deduplicated": True,
            "audit_emitted": True,
        },
    )


def _verify_created_templates(
    ctx: OpsContext,
    *,
    expected: list[dict[str, Any]],
) -> tuple[list[Any], OpsResult | None]:
    from scheduling.task_templates import TaskTemplate

    verified_rows: list[Any] = []
    for spec in expected:
        tid = str(spec.get("id") or "")
        if not tid:
            return [], fail(
                code="verification_failed",
                message="Import verification failed — missing template id.",
            )
        row = TaskTemplate.objects.filter(
            id=tid,
            restaurant=ctx.restaurant,
            is_active=True,
        ).first()
        if not row:
            return [], fail(
                code="verification_failed",
                message="Import verification failed — template not found in database.",
            )
        if row.name != spec.get("name"):
            return [], fail(
                code="verification_failed",
                message="Import verification failed — template name mismatch.",
            )
        want_count = int(spec.get("tasks_count") or 0)
        if want_count and len(row.tasks or []) != want_count:
            return [], fail(
                code="verification_failed",
                message="Import verification failed — task step count mismatch.",
            )
        verified_rows.append(row)
    return verified_rows, None


def _emit_template_import_audit(
    ctx: OpsContext,
    *,
    template,
    operation_key: str,
    skipped: list[dict[str, Any]] | None = None,
) -> None:
    record_operational_audit_event(
        restaurant=ctx.restaurant,
        event_type=TASK_TEMPLATE_IMPORTED,
        entity_type="task_template",
        entity_id=str(template.id),
        entity_label=template.name or "",
        actor=ctx.user,
        location_id=ctx.location_id or "",
        channel=ctx.channel or "agent",
        operation_id=f"{operation_key}:{template.id}",
        idempotency_key=f"{operation_key}:{template.id}",
        new_state=_serialize_template(template),
        summary=f"Process template imported: {template.name}",
        metadata={"skipped": list(skipped or [])},
    )


def import_process_templates(
    ctx: OpsContext,
    *,
    templates: list[dict[str, Any]],
    source_note: str = "",
    skip_duplicates: bool = True,
    operation_id: str = "",
) -> OpsResult:
    """
    Canonical import: authorize → create → DB verify → OperationalEvent.

    Idempotent on operation_id via durable audit rows + short-lived claim lock.
    """
    auth_err = _authorize_template_mutation(ctx, action="import process templates")
    if auth_err:
        return auth_err

    if not templates:
        return fail(
            code="no_templates",
            message="No process templates to import.",
        )

    operation_key = _import_operation_key(operation_id)

    replay = _replay_idempotent_import(ctx, operation_key)
    if replay is not None:
        return replay

    try:
        from miya.services.message_pipeline import claim_mutation_once

        if not claim_mutation_once(operation_key, ttl_seconds=300):
            replay = _replay_idempotent_import(ctx, operation_key)
            if replay is not None:
                return replay
            return ok(
                message="That import is already in progress or was just applied.",
                verified=True,
                code="duplicate_suppressed",
                data={"operation": "import_process_templates", "deduplicated": True},
            )
    except Exception:
        logger.exception("process import idempotency claim failed")

    from scheduling.process_template_import_service import bulk_create_task_templates

    raw = bulk_create_task_templates(
        ctx.restaurant,
        templates,
        acting_user=ctx.user,
        skip_duplicates=skip_duplicates,
        source_note=source_note,
    )
    created_specs = list(raw.get("created") or [])
    skipped = list(raw.get("skipped") or [])
    errors = list(raw.get("errors") or [])

    if errors and not created_specs:
        return fail(
            code="import_failed",
            message="Could not import any process templates.",
            data={"errors": errors, "skipped": skipped},
        )

    verified_rows, verr = _verify_created_templates(ctx, expected=created_specs)
    if verr is not None:
        return verr

    for row in verified_rows:
        _emit_template_import_audit(ctx, template=row, operation_key=operation_key, skipped=skipped)

    try:
        cache.delete(f"agent:sched:task_templates:{ctx.restaurant.id}")
    except Exception:
        pass

    if not verified_rows and skipped:
        return ok(
            message=(
                f"All {len(skipped)} process(es) already exist under the same names — "
                "nothing new was created."
            ),
            verified=True,
            data={
                "created": [],
                "skipped": skipped,
                "operation": "import_process_templates",
                "operation_id": operation_key,
                "audit_emitted": False,
            },
        )

    if not verified_rows:
        return fail(
            code="verification_failed",
            message="No processes were imported.",
            data={"skipped": skipped, "errors": errors},
        )

    names = ", ".join(r.name for r in verified_rows[:5])
    extra = f" (+{len(verified_rows) - 5} more)" if len(verified_rows) > 5 else ""
    msg = f"Imported {len(verified_rows)} process(es) to Processes & Tasks → Templates: {names}{extra}."
    if skipped:
        msg += f" Skipped {len(skipped)} duplicate name(s)."

    return ok(
        message=msg,
        verified=True,
        data={
            "created": [_serialize_template(r) for r in verified_rows],
            "skipped": skipped,
            "errors": errors,
            "operation": "import_process_templates",
            "operation_id": operation_key,
            "audit_emitted": bool(verified_rows),
        },
    )
