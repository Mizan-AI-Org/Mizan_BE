"""Canonical operational status vocabulary — normalize legacy enums here."""
from __future__ import annotations

# Widget / Miya / ops vocabulary (dashboard.Task native + normalized scheduling)
CANONICAL_TASK_STATUSES = frozenset(
    {
        "PENDING",
        "ACCEPTED",
        "IN_PROGRESS",
        "COMPLETED",
        "UNABLE_TO_COMPLETE",
        "CANCELLED",
    }
)

CANONICAL_OPEN_TASK_STATUSES = frozenset(
    {"PENDING", "ACCEPTED", "IN_PROGRESS", "UNABLE_TO_COMPLETE"}
)

# staff.StaffRequest native statuses treated as open/actionable for reads
STAFF_REQUEST_OPEN_STATUSES = frozenset({"PENDING", "ESCALATED", "APPROVED", "WAITING_ON"})

_STAFF_TO_CANONICAL = {
    "PENDING": "PENDING",
    "ESCALATED": "PENDING",
    "APPROVED": "IN_PROGRESS",
    "WAITING_ON": "IN_PROGRESS",
    "REJECTED": "CANCELLED",
    "CLOSED": "COMPLETED",
}

_CANONICAL_TO_STAFF = {
    "PENDING": "PENDING",
    "ACCEPTED": "PENDING",
    "IN_PROGRESS": "APPROVED",
    "COMPLETED": "CLOSED",
    "UNABLE_TO_COMPLETE": "WAITING_ON",
    "CANCELLED": "REJECTED",
}


def is_task_open(status: str | None, *, origin: str = "dashboard") -> bool:
    """Shared predicate — is this task status actionable/open for completion?"""
    if origin == "staff_request":
        raw = (status or "").strip().upper()
        return raw in STAFF_REQUEST_OPEN_STATUSES
    normalized = normalize_task_status(status, origin=origin)
    return normalized in CANONICAL_OPEN_TASK_STATUSES


def normalize_staff_request_status(raw: str | None) -> str:
    val = (raw or "").strip().upper()
    return _STAFF_TO_CANONICAL.get(val, val)


def staff_request_status_from_canonical(raw: str | None) -> str:
    val = normalize_task_status(raw, origin="dashboard")
    return _CANONICAL_TO_STAFF.get(val, "PENDING")

# scheduling.Task native → canonical
_SCHEDULING_TO_CANONICAL = {
    "TODO": "PENDING",
    "IN_PROGRESS": "IN_PROGRESS",
    "COMPLETED": "COMPLETED",
    "CANCELLED": "CANCELLED",
}

# canonical → scheduling.Task native (writes to scheduling only)
_CANONICAL_TO_SCHEDULING = {
    "PENDING": "TODO",
    "ACCEPTED": "TODO",  # scheduling has no ACCEPTED — closest open state
    "IN_PROGRESS": "IN_PROGRESS",
    "COMPLETED": "COMPLETED",
    "UNABLE_TO_COMPLETE": "IN_PROGRESS",
    "CANCELLED": "CANCELLED",
}

# Aliases used in NL / tools
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
    "OPEN": "PENDING",
    "TODO": "PENDING",
}


def normalize_task_status(raw: str | None, *, origin: str = "dashboard") -> str:
    """Map any task status string to canonical vocabulary."""
    val = (raw or "").strip().upper()
    if not val:
        return "PENDING"
    if origin == "scheduling":
        val = _SCHEDULING_TO_CANONICAL.get(val, val)
    return _STATUS_ALIASES.get(val, val)


def normalize_scheduling_task_status(raw: str | None) -> str:
    return normalize_task_status(raw, origin="scheduling")


def scheduling_status_from_canonical(raw: str | None) -> str:
    """Map canonical status → scheduling.Task STATUS_CHOICES value."""
    val = normalize_task_status(raw, origin="dashboard")
    return _CANONICAL_TO_SCHEDULING.get(val, val)
