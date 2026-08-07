"""
Mizan canonical entity layer — single source of truth definitions.

Dashboard, WhatsApp, Miya, and agent APIs should READ through these facades
and WRITE through the canonical service paths declared in the registry.

Non-destructive Phase 2: unified reads + explicit registry; legacy models remain.
"""

from core.canonical.registry import CANONICAL_ENTITIES, CanonicalEntity
from core.canonical.status import (
    CANONICAL_TASK_STATUSES,
    normalize_scheduling_task_status,
    normalize_task_status,
    scheduling_status_from_canonical,
)
from core.canonical.tasks import (
    find_canonical_tasks,
    resolve_canonical_task,
    serialize_canonical_task,
)

__all__ = [
    "CANONICAL_ENTITIES",
    "CanonicalEntity",
    "CANONICAL_TASK_STATUSES",
    "normalize_scheduling_task_status",
    "normalize_task_status",
    "scheduling_status_from_canonical",
    "find_canonical_tasks",
    "resolve_canonical_task",
    "serialize_canonical_task",
]
