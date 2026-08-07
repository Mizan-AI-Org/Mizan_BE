"""
Unified task read facade — dashboard.Task + scheduling.Task.

Single read path for Miya, dashboard search, and future channels.
Mutations remain on origin-specific writers until Phase 15.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from django.db.models import Q

from core.canonical.status import (
    CANONICAL_OPEN_TASK_STATUSES,
    STAFF_REQUEST_OPEN_STATUSES,
    normalize_scheduling_task_status,
    normalize_staff_request_status,
    normalize_task_status,
    staff_request_status_from_canonical,
)

TaskOrigin = Literal["dashboard", "scheduling", "staff_request"]

_ORIGIN_DASHBOARD: TaskOrigin = "dashboard"
_ORIGIN_SCHEDULING: TaskOrigin = "scheduling"
_ORIGIN_STAFF_REQUEST: TaskOrigin = "staff_request"


def _short_ref(task_id) -> str:
    return str(task_id).replace("-", "").upper()[-8:]


def is_record_id(value: str) -> bool:
    """True when value looks like a UUID or short task ref — not a title phrase."""
    import uuid

    v = (value or "").strip().lstrip("#")
    if not v or " " in v:
        return False
    try:
        uuid.UUID(v)
        return True
    except ValueError:
        pass
    compact = v.replace("-", "")
    return len(compact) >= 6 and compact.isalnum()


def _assignee_name_from_user(user) -> str:
    if not user:
        return ""
    name = f"{(getattr(user, 'first_name', None) or '').strip()} {(getattr(user, 'last_name', None) or '').strip()}".strip()
    return name or getattr(user, "email", "") or ""


def serialize_canonical_task(task, *, origin: TaskOrigin) -> dict[str, Any]:
    """Normalize any task row into the ops/Miya vocabulary."""
    if origin == _ORIGIN_STAFF_REQUEST:
        assignee = getattr(task, "assignee", None) or getattr(task, "staff", None)
        status = normalize_staff_request_status(task.status)
        title = (getattr(task, "subject", None) or getattr(task, "description", None) or "").strip()
        return {
            "id": str(task.id),
            "task_ref": f"#{_short_ref(task.id)}",
            "title": title[:255] or "Staff request",
            "description": getattr(task, "description", "") or "",
            "status": status,
            "priority": getattr(task, "priority", "MEDIUM"),
            "category": getattr(task, "category", "") or "",
            "assignee_id": str(assignee.id) if assignee else None,
            "assignee_name": _assignee_name_from_user(assignee) or (getattr(task, "staff_name", None) or None),
            "due_date": None,
            "updated_at": task.updated_at.isoformat() if getattr(task, "updated_at", None) else None,
            "origin": origin,
            "kind": origin,
            "source_label": "Inbox",
            "location_id": None,
        }
    if origin == _ORIGIN_SCHEDULING:
        assignees_qs = task.assigned_to.all() if getattr(task, "pk", None) else []
        if hasattr(assignees_qs, "first"):
            assignee = assignees_qs.first()
        else:
            assignee = assignees_qs[0] if assignees_qs else None
        status = normalize_scheduling_task_status(task.status)
        shift = getattr(task, "assigned_shift", None)
        if shift is not None:
            shift_date = getattr(shift, "shift_date", None)
            source_label = (
                f"Shift · {shift_date.strftime('%a %b %d')}" if shift_date else "Shift"
            )
        else:
            source_label = "Scheduling"
        return {
            "id": str(task.id),
            "task_ref": f"#{_short_ref(task.id)}",
            "title": task.title,
            "description": getattr(task, "description", "") or "",
            "status": status,
            "priority": task.priority,
            "category": getattr(getattr(task, "category", None), "name", "") or "",
            "assignee_id": str(assignee.id) if assignee else None,
            "assignee_name": _assignee_name_from_user(assignee) or None,
            "due_date": task.due_date.isoformat() if getattr(task, "due_date", None) else None,
            "updated_at": task.updated_at.isoformat() if getattr(task, "updated_at", None) else None,
            "origin": origin,
            "kind": origin,
            "source_label": source_label,
        }

    assignee = getattr(task, "assigned_to", None)
    loc_id = getattr(task, "location_id", None)
    return {
        "id": str(task.id),
        "task_ref": f"#{_short_ref(task.id)}",
        "title": task.title,
        "description": getattr(task, "description", "") or "",
        "status": normalize_task_status(task.status, origin=origin),
        "priority": task.priority,
        "category": getattr(task, "category", "") or "",
        "assignee_id": str(assignee.id) if assignee else None,
        "assignee_name": _assignee_name_from_user(assignee) or None,
        "due_date": task.due_date.isoformat() if getattr(task, "due_date", None) else None,
        "updated_at": task.updated_at.isoformat() if getattr(task, "updated_at", None) else None,
        "origin": origin,
        "kind": origin,
        "source_label": "Operations",
        "location_id": str(loc_id) if loc_id else None,
    }


def _match_dashboard_by_id(restaurant, tid: str):
    from dashboard.models import Task

    if not is_record_id(tid):
        return None
    try:
        return Task.objects.select_related("assigned_to").get(pk=tid, restaurant=restaurant)
    except Exception:
        pass
    needle = tid.replace("-", "").upper()
    if len(needle) >= 6:
        for candidate in Task.objects.filter(restaurant=restaurant).select_related("assigned_to")[:300]:
            ref = _short_ref(candidate.id)
            full = str(candidate.id).replace("-", "").upper()
            if ref == needle or full.endswith(needle):
                return candidate
    return None


def _match_scheduling_by_id(restaurant, tid: str):
    from scheduling.task_templates import Task as SchedulingTask

    if not is_record_id(tid):
        return None
    try:
        return SchedulingTask.objects.prefetch_related("assigned_to").get(pk=tid, restaurant=restaurant)
    except Exception:
        pass
    needle = tid.replace("-", "").upper()
    if len(needle) >= 6:
        for candidate in SchedulingTask.objects.filter(restaurant=restaurant).prefetch_related("assigned_to")[:300]:
            ref = _short_ref(candidate.id)
            full = str(candidate.id).replace("-", "").upper()
            if ref == needle or full.endswith(needle):
                return candidate
    return None


def _search_dashboard(restaurant, query: str, *, open_only: bool = False, limit: int = 8):
    from dashboard.models import Task

    qs = (
        Task.objects.filter(restaurant=restaurant)
        .select_related("assigned_to")
        .filter(Q(title__icontains=query) | Q(description__icontains=query))
        .exclude(status="CANCELLED")
        .order_by("-updated_at")
    )
    if open_only:
        qs = qs.filter(status__in=CANONICAL_OPEN_TASK_STATUSES)
    return list(qs[:limit])


def _search_scheduling(restaurant, query: str, *, open_only: bool = False, limit: int = 8):
    from scheduling.task_templates import Task as SchedulingTask

    qs = (
        SchedulingTask.objects.filter(restaurant=restaurant)
        .prefetch_related("assigned_to")
        .filter(Q(title__icontains=query) | Q(description__icontains=query))
        .exclude(status="CANCELLED")
        .order_by("-updated_at")
    )
    if open_only:
        qs = qs.filter(status__in=("TODO", "IN_PROGRESS"))
    return list(qs[:limit])


def _search_staff_requests(restaurant, query: str, *, open_only: bool = False, limit: int = 8):
    try:
        from staff.models import StaffRequest
    except Exception:
        return []

    qs = (
        StaffRequest.objects.filter(restaurant=restaurant)
        .select_related("staff", "assignee")
        .filter(Q(subject__icontains=query) | Q(description__icontains=query))
        .exclude(status="REJECTED")
        .order_by("-updated_at")
    )
    if open_only:
        qs = qs.filter(status__in=STAFF_REQUEST_OPEN_STATUSES)
    return list(qs[:limit])


def _filter_dashboard_by_location(rows: list, *, location_id: str | None, visible_location_ids: list[str] | None):
    if not location_id and not visible_location_ids:
        return rows
    lid = str(location_id) if location_id else None
    visible = {str(x) for x in (visible_location_ids or [])}
    out = []
    for task in rows:
        task_lid = getattr(task, "location_id", None)
        if task_lid is None:
            out.append(task)
        elif lid and str(task_lid) == lid:
            out.append(task)
        elif visible and str(task_lid) in visible:
            out.append(task)
    return out


def _title_matches_query(title: str, query: str) -> bool:
    t = (title or "").strip().lower()
    q = (query or "").strip().lower()
    if not t or not q:
        return False
    if t == q:
        return True
    return q in t or t in q


def resolve_canonical_task(
    restaurant,
    *,
    task_id: str = "",
    q: str = "",
    title: str = "",
    location_id: str | None = None,
    visible_location_ids: list[str] | None = None,
) -> tuple[Any | None, TaskOrigin | None, Any]:
    """
    Resolve a task from either dashboard or scheduling store.

    Returns (task_instance, origin, meta) where meta is None, candidate list, or error str.
    """
    tid = (task_id or "").strip().lstrip("#")
    query = (q or title or "").strip()

    if tid and not is_record_id(tid):
        query = query or tid
        tid = ""

    if tid:
        row = _match_dashboard_by_id(restaurant, tid)
        if row:
            return row, _ORIGIN_DASHBOARD, None
        row = _match_scheduling_by_id(restaurant, tid)
        if row:
            return row, _ORIGIN_SCHEDULING, None
        if len(tid) >= 3 and not re.fullmatch(r"[0-9a-fA-F-]{8,}", tid):
            query = query or tid

    if query:
        db_matches = _filter_dashboard_by_location(
            _search_dashboard(restaurant, query),
            location_id=location_id,
            visible_location_ids=visible_location_ids,
        )
        sched_matches = _search_scheduling(restaurant, query)
        sr_matches = _search_staff_requests(restaurant, query)
        combined: list[tuple[Any, TaskOrigin]] = [
            *((t, _ORIGIN_DASHBOARD) for t in db_matches),
            *((t, _ORIGIN_SCHEDULING) for t in sched_matches),
            *((t, _ORIGIN_STAFF_REQUEST) for t in sr_matches),
        ]
        if len(combined) == 1:
            task, origin = combined[0]
            return task, origin, None
        if len(combined) > 1:
            exact = [
                (t, o)
                for t, o in combined
                if _title_matches_query(getattr(t, "title", None) or getattr(t, "subject", ""), query)
            ]
            if len(exact) == 1:
                return exact[0][0], exact[0][1], None
            candidates = [
                serialize_canonical_task(t, origin=o) for t, o in combined[:8]
            ]
            return None, None, candidates

        # Scoped search missed — distinguish global absence vs wrong establishment.
        if location_id or visible_location_ids:
            global_db = _search_dashboard(restaurant, query)
            global_sr = _search_staff_requests(restaurant, query)
            global_combined: list[tuple[Any, TaskOrigin]] = [
                *((t, _ORIGIN_DASHBOARD) for t in global_db),
                *((t, _ORIGIN_STAFF_REQUEST) for t in global_sr),
            ]
            if global_combined:
                rows = [serialize_canonical_task(t, origin=o) for t, o in global_combined[:5]]
                loc_names = []
                try:
                    from accounts.models import BusinessLocation

                    for row in rows:
                        lid = row.get("location_id")
                        if lid:
                            loc = BusinessLocation.objects.filter(pk=lid).first()
                            if loc:
                                loc_names.append(loc.name)
                except Exception:
                    pass
                hint = loc_names[0] if len(loc_names) == 1 else ""
                msg = f"wrong_establishment:{query}"
                if hint:
                    msg = f"wrong_establishment:{query}:{hint}"
                return None, None, msg
        return None, None, f"not_found:{query}"

    return None, None, "missing"


def find_canonical_tasks(
    restaurant,
    *,
    q: str = "",
    status: str = "",
    assignee_name: str = "",
    assignee_id: str = "",
    limit: int = 20,
    mine_only: bool = False,
    user_id: str | None = None,
    location_id: str | None = None,
    visible_location_ids: list[str] | None = None,
    include_scheduling: bool = True,
) -> list[dict[str, Any]]:
    """List tasks from all canonical read sources, normalized and merged."""
    from dashboard.models import Task

    raw_status = (status or "").strip().upper()
    rows: list[dict[str, Any]] = []

    qs = Task.objects.filter(restaurant=restaurant).select_related("assigned_to")
    if location_id:
        qs = qs.filter(Q(location_id=location_id) | Q(location_id__isnull=True))
    elif visible_location_ids:
        qs = qs.filter(Q(location_id__in=visible_location_ids) | Q(location_id__isnull=True))

    if raw_status in ("ALL", "*"):
        pass
    elif raw_status in ("OPEN", "ACTIVE", ""):
        qs = qs.filter(status__in=CANONICAL_OPEN_TASK_STATUSES)
    else:
        from core.canonical.status import normalize_task_status as norm

        qs = qs.filter(status=norm(raw_status))

    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q))
    if mine_only and user_id:
        qs = qs.filter(Q(assigned_to_id=user_id) | Q(assignees__id=user_id)).distinct()
    elif assignee_id:
        qs = qs.filter(Q(assigned_to_id=assignee_id) | Q(assignees__id=assignee_id)).distinct()
    if assignee_name:
        tokens = [t for t in re.split(r"\s+", assignee_name.strip()) if t]
        for tok in tokens:
            qs = qs.filter(
                Q(assigned_to__first_name__icontains=tok)
                | Q(assigned_to__last_name__icontains=tok)
                | Q(assignees__first_name__icontains=tok)
                | Q(assignees__last_name__icontains=tok)
            ).distinct()

    for task in qs.order_by("-updated_at")[: max(1, min(limit, 40))]:
        rows.append(serialize_canonical_task(task, origin=_ORIGIN_DASHBOARD))

    if len(rows) < limit:
        try:
            from staff.models import StaffRequest

            sr_qs = StaffRequest.objects.filter(restaurant=restaurant).select_related("staff", "assignee")
            if raw_status in ("ALL", "*"):
                pass
            elif raw_status in ("OPEN", "ACTIVE", ""):
                sr_qs = sr_qs.filter(status__in=STAFF_REQUEST_OPEN_STATUSES)
            elif raw_status:
                from core.canonical.status import staff_request_status_from_canonical

                sr_qs = sr_qs.filter(status=staff_request_status_from_canonical(raw_status))
            if q:
                sr_qs = sr_qs.filter(Q(subject__icontains=q) | Q(description__icontains=q))
            if assignee_name:
                tokens = [t for t in re.split(r"\s+", assignee_name.strip()) if t]
                for tok in tokens:
                    sr_qs = sr_qs.filter(
                        Q(assignee__first_name__icontains=tok)
                        | Q(assignee__last_name__icontains=tok)
                        | Q(staff__first_name__icontains=tok)
                        | Q(staff__last_name__icontains=tok)
                        | Q(staff_name__icontains=tok)
                    ).distinct()
            remaining = max(0, limit - len(rows))
            for req in sr_qs.order_by("-updated_at")[:remaining]:
                rows.append(serialize_canonical_task(req, origin=_ORIGIN_STAFF_REQUEST))
        except Exception:
            pass

    if include_scheduling and len(rows) < limit:
        try:
            from scheduling.task_templates import Task as SchedulingTask

            sched_qs = SchedulingTask.objects.filter(restaurant=restaurant).prefetch_related("assigned_to")
            if raw_status in ("ALL", "*"):
                pass
            elif raw_status in ("OPEN", "ACTIVE", ""):
                sched_qs = sched_qs.filter(status__in=("TODO", "IN_PROGRESS"))
            elif raw_status:
                from core.canonical.status import scheduling_status_from_canonical

                sched_qs = sched_qs.filter(status=scheduling_status_from_canonical(raw_status))
            if q:
                sched_qs = sched_qs.filter(Q(title__icontains=q) | Q(description__icontains=q))
            if mine_only and user_id:
                sched_qs = sched_qs.filter(assigned_to__id=user_id).distinct()
            elif assignee_id:
                sched_qs = sched_qs.filter(assigned_to__id=assignee_id).distinct()
            if assignee_name:
                tokens = [t for t in re.split(r"\s+", assignee_name.strip()) if t]
                for tok in tokens:
                    sched_qs = sched_qs.filter(
                        Q(assigned_to__first_name__icontains=tok)
                        | Q(assigned_to__last_name__icontains=tok)
                    ).distinct()

            remaining = max(0, limit - len(rows))
            for task in sched_qs.order_by("-updated_at")[:remaining]:
                rows.append(serialize_canonical_task(task, origin=_ORIGIN_SCHEDULING))
        except Exception:
            pass

    return rows
