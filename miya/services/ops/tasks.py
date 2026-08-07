"""Canonical task find / create / assign / status update with verify."""
from __future__ import annotations

import re
from typing import Any

from django.db.models import Q
from django.utils import timezone

from miya.services.ops.context import (
    OpsContext,
    require_permission,
    require_restaurant,
    require_task_status_permission,
    user_can_read_task,
    user_is_task_assignee,
)
from miya.services.ops.result import OpsResult, clarify, fail, ok
from core.operational_audit.service import (
    TASK_ASSIGNED,
    TASK_CREATED,
    TASK_REASSIGNED,
    TASK_UPDATED,
    task_status_event_type,
)


_OPEN = ("PENDING", "ACCEPTED", "IN_PROGRESS", "UNABLE_TO_COMPLETE")
_STATUS_ALIASES = {
    "DONE": "COMPLETED",
    "COMPLETE": "COMPLETED",
    "FINISHED": "COMPLETED",
    "CLOSE": "COMPLETED",
    "CLOSED": "COMPLETED",
    "STARTED": "IN_PROGRESS",
    "START": "IN_PROGRESS",
    "ACCEPT": "ACCEPTED",
    "CANCEL": "CANCELLED",
    "NEW": "PENDING",
}


def _short_ref(task_id) -> str:
    return str(task_id).replace("-", "").upper()[-8:]


def _serialize_task(task, *, origin: str = "dashboard") -> dict[str, Any]:
    from core.canonical.tasks import serialize_canonical_task

    return serialize_canonical_task(task, origin=origin)  # type: ignore[arg-type]


def _location_scope_ids(ctx) -> tuple[str | None, list[str] | None]:
    visible_ids = [r["id"] for r in ctx.available_locations] if len(ctx.available_locations or []) > 1 else None
    return (ctx.location_id or None), visible_ids


def _format_task_resolve_error(meta: Any, *, query: str = "", establishment_name: str = "") -> str:
    if isinstance(meta, str) and meta.startswith("wrong_establishment:"):
        parts = meta.split(":", 2)
        other = parts[2] if len(parts) > 2 else ""
        q = parts[1] if len(parts) > 1 else query
        here = establishment_name or "the selected establishment"
        if other:
            return (
                f"I found *{q}* at *{other}*, not at {here}. "
                "Switch to that establishment or pick the correct one."
            )
        return (
            f"I found *{q}* at another establishment, not at {here}. "
            "Switch context to the branch where the task lives."
        )
    if isinstance(meta, str) and meta.startswith("not_found:"):
        q = meta.split(":", 1)[-1] or query
        return f"I couldn't find an open task matching '{q}'."
    return ""


def _resolve_task(ctx: OpsContext, *, task_id: str = "", q: str = "", title: str = ""):
    """
    Resolve task for mutations — dashboard.Task only.
    Scheduling tasks are visible via unified read but mutate via dashboard PATCH router.
    """
    from core.canonical.tasks import resolve_canonical_task

    loc_id, visible_ids = _location_scope_ids(ctx)
    task, origin, meta = resolve_canonical_task(
        ctx.restaurant,
        task_id=task_id,
        q=q,
        title=title,
        location_id=loc_id,
        visible_location_ids=visible_ids,
    )
    if task is not None and origin == "scheduling":
        row = _serialize_task(task, origin=origin)
        return None, [
            {
                **row,
                "needs_dashboard_router": True,
                "message": (
                    f"“{row['title']}” is a scheduling task — "
                    "update it from Task Management or ask me to create an ops task instead."
                ),
            }
        ]
    if task is not None:
        return task, None
    err = _format_task_resolve_error(meta, query=q or title, establishment_name=ctx.location_name or "")
    if err:
        return None, err
    return None, meta


def find_tasks(
    ctx: OpsContext,
    *,
    q: str = "",
    status: str = "",
    assignee_name: str = "",
    assignee_id: str = "",
    task_id: str = "",
    include_custom_widgets: bool = True,
    limit: int = 20,
    mine_only: bool = False,
) -> OpsResult:
    # Staff listing own tasks needs no manage_widgets; managers do for tenant-wide search.
    if mine_only or (assignee_id and str(assignee_id) == str(ctx.user_id)):
        err = require_restaurant(ctx)
    else:
        err = require_restaurant(ctx) or require_permission(ctx, "manage_widgets")
    if err:
        return err

    from miya.services.ops.context import require_establishment_context
    from miya.services.ops.scoping import apply_location_scope, filter_visible_location_ids

    if not task_id:
        est_err = require_establishment_context(ctx, for_action="tasks")
        if est_err:
            return est_err

    from core.canonical.tasks import find_canonical_tasks, resolve_canonical_task

    if task_id or (q and len(q.strip()) >= 2 and status == "" and not assignee_name and not assignee_id and not mine_only):
        loc_id, visible_ids = _location_scope_ids(ctx)
        task, origin, meta = resolve_canonical_task(
            ctx.restaurant,
            task_id=task_id,
            q=q,
            location_id=loc_id,
            visible_location_ids=visible_ids,
        )
        if task:
            if origin == "scheduling":
                row = _serialize_task(task, origin=origin)
                return ok(
                    message=(
                        f"{row['task_ref']} *{row['title']}* ({row.get('source_label') or 'Scheduling'}) "
                        f"is **{row['status']}**."
                    ),
                    verified=True,
                    data={"count": 1, "tasks": [row], "task": row, "origin": origin},
                )
            from miya.services.ops.context import guard_entity_location

            loc_err = guard_entity_location(ctx, task)
            if loc_err:
                return loc_err
            if mine_only and not user_is_task_assignee(task, ctx.user):
                return fail(code="permission_denied", message="That task isn't assigned to you.")
            row = _serialize_task(task, origin=origin or "dashboard")
            return ok(
                message=(
                    f"{row['task_ref']} *{row['title']}* is {row['status']}"
                    + (f", assigned to {row['assignee_name']}" if row.get("assignee_name") else "")
                    + "."
                ),
                verified=True,
                data={"count": 1, "tasks": [row], "task": row, "origin": origin},
            )
        if isinstance(meta, list):
            return clarify(
                message="Several tasks match — which one do you mean?",
                data={"candidates": meta, "count": len(meta)},
            )
        wrong = _format_task_resolve_error(meta, query=q or task_id, establishment_name=ctx.location_name or "")
        if wrong:
            return fail(code="task_wrong_establishment", message=wrong)
        if isinstance(meta, str) and meta.startswith("not_found:"):
            return fail(code="task_not_found", message=f"I couldn't find a task matching '{q or task_id}'.")

    visible_ids = [r["id"] for r in ctx.available_locations] if len(ctx.available_locations) > 1 else None
    rows = find_canonical_tasks(
        ctx.restaurant,
        q=q,
        status=status,
        assignee_name=assignee_name,
        assignee_id=assignee_id,
        limit=max(1, min(limit, 40)),
        mine_only=mine_only or (assignee_id and str(assignee_id) == str(ctx.user_id)),
        user_id=str(ctx.user_id) if ctx.user_id else None,
        location_id=ctx.location_id or None,
        visible_location_ids=visible_ids,
        include_scheduling=True,
    )
    if not include_custom_widgets:
        rows = [r for r in rows if r.get("origin") == "dashboard"]
    if not rows:
        return fail(code="task_not_found", message="No matching tasks found.", data={"tasks": []})
    return ok(
        message=f"Found {len(rows)} task(s).",
        verified=True,
        data={"count": len(rows), "tasks": rows},
    )


def get_task_state(
    ctx: OpsContext,
    *,
    task_id: str = "",
    q: str = "",
    title: str = "",
    assignee_name: str = "",
    mine_only: bool = False,
) -> OpsResult:
    """Retrieve current DB state — never invent status."""
    err = require_restaurant(ctx)
    if err:
        return err

    # Cross-assignee status queries (e.g. "Is Ahmed's task done?") require manager scope.
    if assignee_name and not mine_only:
        err = require_permission(ctx, "manage_widgets")
        if err:
            return err

    # "Is Ahmed's task completed?" → look up by assignee when no explicit title/id
    if assignee_name and not task_id and not title:
        by_assignee = find_tasks(
            ctx,
            assignee_name=assignee_name,
            q=q if q and q.lower() != assignee_name.lower() else "",
            status="ALL",
            limit=8,
        )
        if by_assignee.success:
            tasks = (by_assignee.data or {}).get("tasks") or []
            open_ones = [t for t in tasks if t.get("status") in _OPEN]
            pick = open_ones[0] if len(open_ones) == 1 else (tasks[0] if len(tasks) == 1 else None)
            if pick:
                return ok(
                    message=(
                        f"{pick.get('task_ref')} *{pick.get('title')}* — status **{pick.get('status')}**"
                        + (f", assignee {pick.get('assignee_name')}" if pick.get("assignee_name") else "")
                        + "."
                    ),
                    verified=True,
                    data={"task": pick, "tasks": [pick], "count": 1},
                )
            if len(tasks) > 1:
                return clarify(
                    message=f"{assignee_name} has several tasks — which one?",
                    data={"candidates": tasks[:5]},
                )
        # fall through to title/q resolve

    task, meta = _resolve_task(ctx, task_id=task_id, q=q or title, title=title)
    from core.canonical.tasks import resolve_canonical_task

    if not task:
        loc_id, visible_ids = _location_scope_ids(ctx)
        task, origin, canon_meta = resolve_canonical_task(
            ctx.restaurant,
            task_id=task_id,
            q=q or title,
            title=title,
            location_id=loc_id,
            visible_location_ids=visible_ids,
        )
        if task and origin == "scheduling":
            row = _serialize_task(task, origin=origin)
            return ok(
                message=(
                    f"{row['task_ref']} *{row['title']}* — status **{row['status']}**"
                    + (f", assignee {row['assignee_name']}" if row.get("assignee_name") else ", unassigned")
                    + f" ({row.get('source_label') or 'Scheduling'})."
                ),
                verified=True,
                data={"task": row, "tasks": [row], "count": 1, "origin": origin},
            )
        if canon_meta and not meta:
            meta = canon_meta

    if task:
        if not mine_only and not user_can_read_task(ctx, task):
            return fail(
                code="permission_denied",
                message="You can only check the status of tasks assigned to you.",
                miya_directive="Do not reveal other staff members' task details.",
            )
        origin = "staff_request" if task.__class__.__name__ == "StaffRequest" else "dashboard"
        row = _serialize_task(task, origin=origin)
        return ok(
            message=(
                f"{row['task_ref']} *{row['title']}* — status **{row['status']}**"
                + (f", assignee {row['assignee_name']}" if row.get("assignee_name") else ", unassigned")
                + "."
            ),
            verified=True,
            data={"task": row, "tasks": [row], "count": 1},
        )
    if isinstance(meta, list):
        return clarify(
            message="Several tasks match — which task do you mean?",
            data={"candidates": meta},
        )
    wrong = _format_task_resolve_error(meta if isinstance(meta, str) else "", query=q or title, establishment_name=ctx.location_name or "")
    if wrong:
        return fail(code="task_wrong_establishment", message=wrong)
    if meta == "missing" and not assignee_name:
        return clarify(
            message="Which task? Give me the title or the short ref (e.g. #7FFC0D68).",
        )
    return fail(code="task_not_found", message="I couldn't find that task in the database.")


def create_task(
    ctx: OpsContext,
    *,
    title: str,
    assignee_name: str = "",
    assignee_id: str = "",
    description: str = "",
    priority: str = "MEDIUM",
    category: str = "",
    source_text: str = "",
) -> OpsResult:
    err = require_restaurant(ctx) or require_permission(ctx, "manage_widgets")
    if err:
        return err

    title = (title or "").strip()
    if not title:
        return fail(code="title_required", message="I need a task title before I can create it.")

    # Establishment before assignee — never create (or resolve staff) without scope.
    from miya.services.ops.context import require_establishment_context

    est_err = require_establishment_context(ctx, for_action="creating a task")
    if est_err:
        return est_err

    if not assignee_name and not assignee_id:
        return clarify(
            message="Who should I assign this to? Give me a staff name.",
            data={"pending_title": title},
        )

    from dashboard.views_agent import _resolve_assignee
    from dashboard.models import Task
    from dashboard.task_assign_notify import notify_task_assignment
    from dashboard.task_sync import broadcast_tasks_invalidate

    assignee, aerr = _resolve_assignee(
        {"assignee_name": assignee_name, "assignee_id": assignee_id, "name": assignee_name},
        ctx.restaurant,
    )
    if aerr or not assignee:
        return fail(
            code="assignee_not_found",
            message=aerr or f"I couldn't find staff matching '{assignee_name}'.",
        )

    # Manager self-assign block
    if str(assignee.id) == ctx.user_id and ctx.role in ("OWNER", "MANAGER", "ADMIN"):
        return fail(
            code="manager_self_task_blocked",
            message="I won't assign a dashboard task to you — name a staff member instead.",
        )

    cat = (category or "").strip().upper() or None

    create_kwargs = dict(
        restaurant=ctx.restaurant,
        assigned_to=assignee,
        created_by=ctx.user if getattr(ctx.user, "pk", None) else None,
        title=title[:255],
        description=(description or source_text or "")[:4000] or None,
        priority=(priority or "MEDIUM").upper() if (priority or "").upper() in ("LOW", "MEDIUM", "HIGH", "URGENT") else "MEDIUM",
        status="PENDING",
        source="MIYA" if ctx.channel != "whatsapp" else "WHATSAPP",
        category=cat,
    )
    if ctx.location_id:
        create_kwargs["location_id"] = ctx.location_id
    task = Task.objects.create(**create_kwargs)
    task.assignees.add(assignee)
    try:
        notify_task_assignment(
            task,
            assignee=assignee,
            sender=ctx.user if getattr(ctx.user, "pk", None) else None,
        )
    except Exception:
        pass
    try:
        broadcast_tasks_invalidate(ctx.restaurant, reason="task_created", task_id=str(task.id))
    except Exception:
        pass

    # VERIFY
    fresh = Task.objects.select_related("assigned_to").filter(id=task.id, restaurant=ctx.restaurant).first()
    if not fresh or fresh.assigned_to_id != assignee.id or fresh.status != "PENDING":
        return fail(
            code="verify_failed",
            message="I tried to create the task but couldn't verify it was saved correctly.",
        )

    row = _serialize_task(fresh)
    _emit_task_audit(
        ctx,
        TASK_CREATED,
        fresh,
        summary=f"Task created: {fresh.title}",
        operation_id=f"task:create:{fresh.id}",
    )
    return ok(
        message=(
            f"Created {row['task_ref']} *{row['title']}* for {row['assignee_name']} "
            f"(status {row['status']}). WhatsApp notification sent."
        ),
        verified=True,
        data={"task": row, "tasks": [row], "audit_emitted": True},
    )


def assign_task(
    ctx: OpsContext,
    *,
    assignee_name: str = "",
    assignee_id: str = "",
    task_id: str = "",
    q: str = "",
    title: str = "",
) -> OpsResult:
    err = require_restaurant(ctx) or require_permission(ctx, "manage_widgets")
    if err:
        return err

    if not assignee_name and not assignee_id:
        return clarify(message="Who should I assign it to?")

    # Resolve task — if pronoun-only missing, ask
    if not task_id and not q and not title:
        return clarify(
            message="Which task should I assign? Tell me the title or short ref.",
        )

    task, meta = _resolve_task(ctx, task_id=task_id, q=q or title)
    if isinstance(meta, list):
        return clarify(
            message="Several tasks match — which one should I assign?",
            data={"candidates": meta},
        )
    if not task:
        return fail(code="task_not_found", message="I couldn't find that task in the database.")

    from miya.services.ops.context import guard_entity_location

    loc_err = guard_entity_location(ctx, task)
    if loc_err:
        return loc_err

    from dashboard.views_agent import _resolve_assignee
    from dashboard.task_assign_notify import notify_task_reassignment
    from dashboard.task_sync import broadcast_tasks_invalidate

    assignee, aerr = _resolve_assignee(
        {"assignee_name": assignee_name, "assignee_id": assignee_id, "name": assignee_name},
        ctx.restaurant,
    )
    if aerr or not assignee:
        return fail(
            code="assignee_not_found",
            message=aerr or f"I couldn't find staff matching '{assignee_name}'.",
        )

    old = task.assigned_to
    task.assigned_to = assignee
    task.save(update_fields=["assigned_to", "updated_at"])
    # Keep M2M in sync (Phase 2 fix)
    task.assignees.clear()
    task.assignees.add(assignee)

    try:
        notify_task_reassignment(
            task,
            assignee,
            sender=ctx.user if getattr(ctx.user, "pk", None) else None,
            old_assignee=old,
        )
    except Exception:
        try:
            from dashboard.task_assign_notify import notify_task_assignment

            notify_task_assignment(task, assignee=assignee, sender=ctx.user)
        except Exception:
            pass
    try:
        broadcast_tasks_invalidate(ctx.restaurant, reason="task_reassigned", task_id=str(task.id))
    except Exception:
        pass

    from dashboard.models import Task

    fresh = Task.objects.select_related("assigned_to").filter(id=task.id, restaurant=ctx.restaurant).first()
    if not fresh or fresh.assigned_to_id != assignee.id:
        return fail(
            code="verify_failed",
            message=f"I couldn't verify that the task was assigned to {assignee_name or assignee.email}.",
        )
    if not fresh.assignees.filter(id=assignee.id).exists():
        return fail(
            code="verify_failed",
            message="Assignment partially saved — assignee list is out of sync. Please try again.",
        )

    row = _serialize_task(fresh)
    evt = TASK_REASSIGNED if old and old.id != assignee.id else TASK_ASSIGNED
    _emit_task_audit(
        ctx,
        evt,
        fresh,
        previous_state={"assignee_id": str(old.id) if old else None, "assignee_name": getattr(old, "email", None)},
        new_state={"assignee_id": str(assignee.id), "assignee_name": row.get("assignee_name")},
        summary=f"Task assigned to {row.get('assignee_name')}",
        operation_id=f"task:assign:{fresh.id}:{assignee.id}",
    )
    return ok(
        message=f"Assigned {row['task_ref']} *{row['title']}* to {row['assignee_name']} (status {row['status']}).",
        verified=True,
        data={"task": row, "audit_emitted": True},
    )


def _notify_managers_task_outcome(task, acting_user, *, kind: str) -> None:
    """Shared manager notify for COMPLETED / UNABLE (WhatsApp + dashboard)."""
    try:
        from accounts.models import CustomUser
        from notifications.services import notification_service

        restaurant = task.restaurant
        managers = CustomUser.objects.filter(
            restaurant=restaurant,
            is_active=True,
            role__in=("SUPER_ADMIN", "OWNER", "ADMIN", "MANAGER"),
        ).exclude(pk=getattr(acting_user, "id", None))[:8]
        actor = ""
        if acting_user:
            actor = (
                f"{(acting_user.first_name or '').strip()} "
                f"{(acting_user.last_name or '').strip()}"
            ).strip() or acting_user.email
        if kind == "COMPLETED":
            title = "Task completed"
            ntype = "TASK_COMPLETED"
            msg = f"{actor or 'Staff'} completed: {task.title}"
            wa = f"✅ Task completed by {actor or 'staff'}: *{task.title}*"
        else:
            title = "Task unable to complete"
            ntype = "TASK_ASSIGNED"
            msg = f"{actor or 'Staff'} cannot complete: {task.title}"
            wa = f"⚠️ {actor or 'Staff'} marked unable to complete: *{task.title}*"
        for mgr in managers:
            notification_service.send_custom_notification(
                recipient=mgr,
                message=msg,
                title=title,
                notification_type=ntype,
                channels=["app", "push"],
                sender=acting_user,
                location_id=str(getattr(task, "location_id", None) or "") or None,
            )
            if (mgr.phone or "").strip():
                try:
                    notification_service.send_whatsapp_text(mgr.phone, wa)
                except Exception:
                    pass
    except Exception:
        pass


def update_task_status(
    ctx: OpsContext,
    *,
    status: str,
    task_id: str = "",
    q: str = "",
    title: str = "",
    notify_managers: bool | None = None,
    assignee_scope: bool = False,
    operation_id: str = "",
    skip_idempotency: bool = False,
) -> OpsResult:
    """
    Update dashboard.Task status with DB verify.

    ``assignee_scope=True`` (WhatsApp staff): resolve among the user's open tasks first.
    Permission: manage_widgets OR assignee of the task.
    """
    err = require_restaurant(ctx)
    if err:
        return err

    raw = (status or "").strip().upper()
    new_status = _STATUS_ALIASES.get(raw, raw)
    valid = {"PENDING", "ACCEPTED", "IN_PROGRESS", "COMPLETED", "UNABLE_TO_COMPLETE", "CANCELLED"}
    if new_status not in valid:
        return fail(
            code="invalid_status",
            message=f"Status '{status}' isn't valid. Use PENDING, IN_PROGRESS, COMPLETED, or CANCELLED.",
        )

    if not task_id and not q and not title:
        return clarify(message="Which task should I update? Give me the title or short ref.")

    # Staff WhatsApp: prefer assignee-scoped resolve
    task = None
    meta: Any = None
    if assignee_scope and not task_id:
        from dashboard.models import Task

        qs = (
            Task.objects.filter(restaurant=ctx.restaurant, status__in=_OPEN)
            .filter(Q(assigned_to_id=ctx.user_id) | Q(assignees__id=ctx.user_id))
            .distinct()
            .select_related("assigned_to")
            .order_by("-updated_at")
        )
        needle = (q or title or "").strip()
        matches = list(qs[:20])
        if needle:
            low = needle.lower()
            # Short ref in needle
            ref_m = re.search(r"#?([0-9a-f]{6,8})\b", low)
            if ref_m:
                ref = ref_m.group(1).replace("-", "")
                for t in matches:
                    if _short_ref(t.id).lower().endswith(ref) or str(t.id).replace("-", "").lower().endswith(ref):
                        task = t
                        break
            if task is None:
                titled = [t for t in matches if low in (t.title or "").lower() or (t.title or "").lower() in low]
                if len(titled) == 1:
                    task = titled[0]
                elif len(titled) > 1:
                    return clarify(
                        message="Several of your tasks match — which one?",
                        data={"candidates": [_serialize_task(t) for t in titled[:5]]},
                    )
            if task is None:
                task, meta = _resolve_task(ctx, task_id="", q=needle)
                if task and not user_is_task_assignee(task, ctx.user):
                    task, meta = None, "not_found:"
        else:
            if len(matches) == 1:
                task = matches[0]
            elif len(matches) > 1:
                return clarify(
                    message="You have several open tasks — which one? Reply with the title or #ref.",
                    data={"candidates": [_serialize_task(t) for t in matches[:5]]},
                )
            else:
                meta = "not_found:"
    else:
        task, meta = _resolve_task(ctx, task_id=task_id, q=q or title)

    if isinstance(meta, list):
        return clarify(
            message="Several tasks match — which one should I update?",
            data={"candidates": meta},
        )
    if not task:
        if isinstance(meta, str) and meta:
            if meta.startswith("wrong_establishment:") or "another establishment" in meta.lower():
                wrong = meta if not meta.startswith("wrong_establishment:") else _format_task_resolve_error(
                    meta, query=q or title, establishment_name=ctx.location_name or ""
                )
                return fail(code="task_wrong_establishment", message=wrong or meta)
            return fail(code="task_not_found", message=meta)
        return fail(code="task_not_found", message="I couldn't find that task in the database.")

    try:
        from staff.models import StaffRequest
    except Exception:
        StaffRequest = None  # type: ignore

    if StaffRequest is not None and isinstance(task, StaffRequest):
        return _update_staff_request_status(
            ctx,
            task,
            new_status=new_status,
            operation_id=operation_id,
            skip_idempotency=skip_idempotency,
        )

    from miya.services.ops.context import guard_entity_location

    loc_err = guard_entity_location(ctx, task)
    if loc_err:
        return loc_err

    perm = require_task_status_permission(ctx, task)
    if perm:
        return perm

    # Idempotency AFTER resolve — clarify/not-found must not consume the lock
    fingerprint = (operation_id or "").strip()
    if not skip_idempotency:
        try:
            from miya.services.message_pipeline import claim_mutation_once, new_operation_id

            fingerprint = fingerprint or new_operation_id(
                "update_ops_task_status",
                {
                    "task_id": str(task.id),
                    "status": new_status,
                    "user_id": ctx.user_id,
                },
            )
            if not claim_mutation_once(fingerprint, ttl_seconds=90):
                row = _serialize_task(task)
                return ok(
                    message="That status update was already applied (duplicate suppressed).",
                    verified=True,
                    data={
                        "deduplicated": True,
                        "operation_id": fingerprint,
                        "task": row,
                        "status": new_status,
                    },
                    code="duplicate_suppressed",
                )
        except Exception:
            fingerprint = (operation_id or "").strip()

    old = task.status
    # Already at target → treat as success without a second write
    if old == new_status:
        row = _serialize_task(task)
        return ok(
            message=f"{row['task_ref']} *{row['title']}* is already **{row['status']}**.",
            verified=True,
            data={"task": row, "previous_status": old, "idempotent": True, "operation_id": fingerprint or None},
        )

    task.status = new_status
    update_fields = ["status", "updated_at"]
    if new_status == "ACCEPTED":
        meta_rm = dict(getattr(task, "routing_metadata", None) or {})
        meta_rm["acknowledged_by"] = str(ctx.user_id)
        meta_rm["acknowledged_at"] = timezone.now().isoformat()
        task.routing_metadata = meta_rm
        update_fields.append("routing_metadata")
    if new_status == "COMPLETED":
        task.completed_at = timezone.now()
        task.completed_by = ctx.user if getattr(ctx.user, "pk", None) else None
        update_fields.extend(["completed_at", "completed_by"])
    task.save(update_fields=update_fields)

    try:
        from dashboard.task_sync import broadcast_tasks_invalidate

        broadcast_tasks_invalidate(ctx.restaurant, reason="task_status", task_id=str(task.id))
    except Exception:
        pass

    from dashboard.models import Task

    fresh = Task.objects.select_related("assigned_to").filter(id=task.id, restaurant=ctx.restaurant).first()
    if not fresh or fresh.status != new_status:
        return fail(
            code="verify_failed",
            message=f"I couldn't verify the status change to {new_status}. It may still be {old}.",
        )

    should_notify = notify_managers
    if should_notify is None:
        should_notify = ctx.channel == "whatsapp" or assignee_scope
    if should_notify and new_status == "COMPLETED" and old != "COMPLETED":
        _notify_managers_task_outcome(fresh, ctx.user, kind="COMPLETED")
    elif should_notify and new_status == "UNABLE_TO_COMPLETE":
        _notify_managers_task_outcome(fresh, ctx.user, kind="UNABLE")

    row = _serialize_task(fresh)
    evt = task_status_event_type(old, new_status)
    op_key = fingerprint or f"task:status:{fresh.id}:{old}:{new_status}"
    _emit_task_audit(
        ctx,
        evt,
        fresh,
        previous_state={"status": old},
        new_state={"status": new_status},
        summary=f"Task status {old} → {new_status}",
        operation_id=op_key,
    )
    return ok(
        message=f"Updated {row['task_ref']} *{row['title']}* from {old} → **{row['status']}**.",
        verified=True,
        data={
            "task": row,
            "previous_status": old,
            "operation_id": fingerprint or None,
            "operation": "update_task_status",
            "new_status": new_status,
            "audit_emitted": True,
        },
    )


def _update_staff_request_status(
    ctx: OpsContext,
    req,
    *,
    new_status: str,
    operation_id: str = "",
    skip_idempotency: bool = False,
) -> OpsResult:
    """Close/update staff.StaffRequest via canonical ops path."""
    from core.canonical.status import is_task_open, staff_request_status_from_canonical

    perm = require_task_status_permission(ctx, req)
    if perm:
        return perm

    target = staff_request_status_from_canonical(new_status)
    old = req.status
    if old == target:
        row = _serialize_task(req, origin="staff_request")
        return ok(
            message=f"{row['task_ref']} *{row['title']}* is already **{row['status']}**.",
            verified=True,
            data={"task": row, "previous_status": old, "idempotent": True},
        )

    if new_status == "COMPLETED" and not is_task_open(old, origin="staff_request"):
        row = _serialize_task(req, origin="staff_request")
        return ok(
            message=f"{row['task_ref']} *{row['title']}* is already **{row['status']}**.",
            verified=True,
            data={"task": row, "previous_status": old, "idempotent": True},
        )

    fingerprint = (operation_id or "").strip()
    if not skip_idempotency:
        try:
            from miya.services.message_pipeline import claim_mutation_once, new_operation_id

            fingerprint = fingerprint or new_operation_id(
                "update_ops_task_status",
                {"task_id": str(req.id), "status": new_status, "user_id": ctx.user_id, "origin": "staff_request"},
            )
            if not claim_mutation_once(fingerprint, ttl_seconds=90):
                row = _serialize_task(req, origin="staff_request")
                return ok(
                    message="That status update was already applied (duplicate suppressed).",
                    verified=True,
                    data={"deduplicated": True, "task": row, "status": new_status},
                    code="duplicate_suppressed",
                )
        except Exception:
            fingerprint = (operation_id or "").strip()

    req.status = target
    req.save(update_fields=["status", "updated_at"])

    from staff.models import StaffRequest

    fresh = StaffRequest.objects.filter(pk=req.id, restaurant=ctx.restaurant).first()
    if not fresh or fresh.status != target:
        return fail(code="verify_failed", message="I couldn't verify the staff request status change.")

    row = _serialize_task(fresh, origin="staff_request")
    evt = task_status_event_type(old, new_status)
    op_key = fingerprint or f"task:status:{fresh.id}:{old}:{new_status}"
    _emit_task_audit(
        ctx,
        evt,
        fresh,
        previous_state={"status": old},
        new_state={"status": target},
        summary=f"Staff request status {old} → {target}",
        operation_id=op_key,
    )
    return ok(
        message=f"Updated {row['task_ref']} *{row['title']}* from {old} → **{row['status']}**.",
        verified=True,
        data={
            "task": row,
            "previous_status": old,
            "operation_id": fingerprint or None,
            "operation": "update_task_status",
            "new_status": new_status,
            "audit_emitted": True,
            "origin": "staff_request",
        },
    )


def update_task(
    ctx: OpsContext,
    *,
    task_id: str = "",
    q: str = "",
    title: str = "",
    priority: str | None = None,
    due_date: Any = None,
    description: str | None = None,
    require_photo_proof: bool | None = None,
) -> OpsResult:
    """
    Update dashboard.Task fields (priority, due date, title, description, photo proof)
    with DB verify. Mirrors ``agent_update_dashboard_task`` without HTTP self-call.
    """
    err = require_restaurant(ctx) or require_permission(ctx, "manage_widgets")
    if err:
        return err

    if not task_id and not q and not title:
        return clarify(message="Which task should I update? Give me the title or short ref.")

    task, meta = _resolve_task(ctx, task_id=task_id, q=q or title, title=title)
    if isinstance(meta, list):
        return clarify(
            message="Several tasks match — which one should I update?",
            data={"candidates": meta},
        )
    if not task:
        return fail(code="task_not_found", message="I couldn't find that task in the database.")

    from miya.services.ops.context import guard_entity_location

    loc_err = guard_entity_location(ctx, task)
    if loc_err:
        return loc_err

    from dashboard.views_agent import _coerce_bool, _parse_due_date

    update_fields = ["updated_at"]
    changed: list[str] = []
    expected: dict[str, Any] = {}

    if priority is not None and str(priority).strip():
        p = str(priority).upper().strip()
        aliases = {"NORMAL": "MEDIUM", "MED": "MEDIUM", "CRITICAL": "URGENT"}
        p = aliases.get(p, p)
        if p not in ("LOW", "MEDIUM", "HIGH", "URGENT"):
            return fail(
                code="invalid_priority",
                message="Priority must be Low, Medium, High, or Urgent.",
            )
        task.priority = p
        update_fields.append("priority")
        changed.append(f"priority={p}")
        expected["priority"] = p

    if due_date is not None:
        parsed, due_err = _parse_due_date(due_date)
        if due_err:
            return fail(code="invalid_due_date", message=due_err)
        task.due_date = parsed
        update_fields.append("due_date")
        changed.append(f"due={parsed}" if parsed else "due=cleared")
        expected["due_date"] = parsed

    if description is not None:
        task.description = str(description).strip()[:5000]
        update_fields.append("description")
        changed.append("description")

    if title is not None and str(title).strip():
        task.title = str(title).strip()[:200]
        update_fields.append("title")
        changed.append("title")
        expected["title"] = task.title

    if require_photo_proof is not None:
        task.require_photo_proof = _coerce_bool(require_photo_proof, default=task.require_photo_proof)
        update_fields.append("require_photo_proof")
        changed.append(f"photo_proof={task.require_photo_proof}")
        expected["require_photo_proof"] = task.require_photo_proof

    if not changed:
        return clarify(
            message="Tell me what to change — priority, due date, title, description, or photo proof.",
        )

    task.save(update_fields=list(dict.fromkeys(update_fields)))

    try:
        from dashboard.task_sync import broadcast_tasks_invalidate

        broadcast_tasks_invalidate(ctx.restaurant, reason="task_updated", task_id=str(task.id))
    except Exception:
        pass

    from dashboard.models import Task

    fresh = Task.objects.select_related("assigned_to").filter(id=task.id, restaurant=ctx.restaurant).first()
    if not fresh:
        return fail(
            code="verify_failed",
            message="I tried to update the task but couldn't verify it was saved correctly.",
        )

    for key, want in expected.items():
        got = getattr(fresh, key, None)
        if key == "due_date" and want is None and got is None:
            continue
        if str(got) != str(want):
            return fail(
                code="verify_failed",
                message="I couldn't verify the task update in the database.",
                data={"expected": expected, "actual": _serialize_task(fresh)},
            )

    row = _serialize_task(fresh)
    _emit_task_audit(
        ctx,
        TASK_UPDATED,
        fresh,
        new_state={"changed": changed},
        summary=f"Task updated: {', '.join(changed)}",
        operation_id=f"task:update:{fresh.id}:{hash(tuple(changed)) & 0xFFFFFFFF}",
    )
    return ok(
        message=f"Updated {row['task_ref']} *{row['title']}* ({', '.join(changed)}).",
        verified=True,
        data={"task": row, "changed": changed, "operation": "update_task", "audit_emitted": True},
    )


def _emit_task_audit(
    ctx,
    event_type: str,
    task,
    *,
    previous_state=None,
    new_state=None,
    summary: str = "",
    operation_id: str = "",
) -> None:
    try:
        from core.operational_audit.service import record_operational_audit_event

        record_operational_audit_event(
            restaurant=ctx.restaurant,
            event_type=event_type,
            entity_type="task",
            entity_id=str(task.id),
            entity_label=getattr(task, "title", "") or "",
            actor=ctx.user,
            location_id=ctx.location_id or "",
            channel=ctx.channel or "dashboard",
            operation_id=operation_id,
            previous_state=previous_state,
            new_state=new_state,
            summary=summary or f"{event_type}: {getattr(task, 'title', '')}",
        )
    except Exception:
        pass
