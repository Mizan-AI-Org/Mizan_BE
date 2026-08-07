"""
Miya Operational Intelligence Core (Phase 1–2).

Laws:
  - Database is reality; conversation memory is context only.
  - Assistant natural-language text NEVER executes actions.
  - Mutations only via structured tools with operation_id + verification.
  - Memory layers are separated; CURRENT DATABASE STATE wins conflicts.
"""
from __future__ import annotations

from miya.services.intelligence.actions import (
    ACTION_CATALOG,
    execute_structured_action,
    is_structured_action,
)
from miya.services.intelligence.audit import record_audit
from miya.services.intelligence.context_engine import (
    ExecutionContext,
    build_execution_context,
    execution_context_from_session,
)
from miya.services.intelligence.events import emit_ops_event
from miya.services.intelligence.idempotency import (
    claim_message_once,
    claim_operation_once,
)
from miya.services.intelligence.memory import (
    MemoryStore,
    assemble_memory_bundle,
    memory_prompt_block,
    remember_entity_ids,
)
from miya.services.intelligence.memory_priority import MEMORY_PRIORITY, memory_priority_directive
from miya.services.intelligence.operational_memory import (
    recall_operational_memory,
    reconstruct_entity_timeline,
    record_operational_observation,
)
from miya.services.intelligence.reality import (
    get_current_assignment,
    get_current_document,
    get_current_establishment,
    get_current_incident,
    get_current_invoice,
    get_current_meeting,
    get_current_reminder,
    get_current_staff,
    get_current_task,
)
from miya.services.intelligence.verify import verify_mutation
from miya.services.intelligence.working_memory import get_working_memory, update_working_memory

__all__ = [
    "ACTION_CATALOG",
    "ExecutionContext",
    "MEMORY_PRIORITY",
    "MemoryStore",
    "assemble_memory_bundle",
    "build_execution_context",
    "claim_message_once",
    "claim_operation_once",
    "emit_ops_event",
    "execute_structured_action",
    "execution_context_from_session",
    "get_current_assignment",
    "get_current_document",
    "get_current_establishment",
    "get_current_incident",
    "get_current_invoice",
    "get_current_meeting",
    "get_current_reminder",
    "get_current_staff",
    "get_current_task",
    "get_working_memory",
    "is_structured_action",
    "memory_priority_directive",
    "memory_prompt_block",
    "recall_operational_memory",
    "reconstruct_entity_timeline",
    "record_audit",
    "record_operational_observation",
    "remember_entity_ids",
    "update_working_memory",
    "verify_mutation",
]
