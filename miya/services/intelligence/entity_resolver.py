"""
Unified entity resolution — working_memory → working_set → DB lookup.

Conversation memory identifies entities; database state determines truth.
Ambiguity → CLARIFY, never random action.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from miya.services.intelligence.planning.types import EntityType
from miya.services.ops.context import OpsContext


@dataclass
class EntityResolution:
    entity_type: str
    entity_id: str = ""
    candidates: list[dict[str, Any]] | None = None
    clarify_message: str = ""
    source: str = ""  # working_memory | working_set | database | pronoun_index

    @property
    def needs_clarify(self) -> bool:
        return bool(self.clarify_message) or (
            self.candidates is not None and len(self.candidates) > 1 and not self.entity_id
        )


def resolve_entity_reference(
    ctx: OpsContext,
    *,
    entity_type: EntityType | str,
    entity_id: str = "",
    query: str = "",
    pronoun: bool = False,
    pronoun_index: int | None = None,
    session_context: dict[str, Any] | None = None,
) -> EntityResolution:
    """Resolve entity from unified memory layers then DB."""
    et = entity_type.value if hasattr(entity_type, "value") else str(entity_type or "")
    sess = session_context or {}

    if entity_id:
        if et in ("document", "tenant_file", "file"):
            return _resolve_document_entity(
                ctx,
                entity_id=str(entity_id),
                session_context=sess,
            )
        return EntityResolution(entity_type=et, entity_id=str(entity_id), source="explicit_id")

    # Ordinal pronoun: "the second one"
    if pronoun_index is not None and pronoun_index >= 0:
        tid = _from_working_set_index(ctx, "tasks", sess, pronoun_index)
        if tid:
            return EntityResolution(entity_type="task", entity_id=tid, source="working_set_index")
        return EntityResolution(
            entity_type=et,
            clarify_message="Which item do you mean? Tell me the number or title — I won't guess.",
        )

    if pronoun or not query:
        if et in ("task", ""):
            tid = _from_working_memory_task(ctx) or _from_working_set(ctx, "tasks", sess)
            if tid:
                return EntityResolution(entity_type="task", entity_id=tid, source="working_memory_or_set")
        if et in ("invoice", ""):
            iid = _from_working_set(ctx, "invoices", sess)
            if iid:
                return EntityResolution(entity_type="invoice", entity_id=iid, source="working_set")
        if et in ("document", "tenant_file", "file", ""):
            return _resolve_document_entity(
                ctx,
                query=query,
                pronoun=pronoun,
                session_context=sess,
                mutation_sensitive=False,
            )

    if et in ("document", "tenant_file", "file"):
        return _resolve_document_entity(
            ctx,
            query=query,
            pronoun=pronoun,
            session_context=sess,
            mutation_sensitive=_mutation_sensitive_query(query),
        )
    if et == "incident":
        return _resolve_incident_db(ctx, query=query)

    if et == "task" or (not et and query):
        return _resolve_task_db(ctx, query=query, pronoun=pronoun, session_context=sess)

    return EntityResolution(entity_type=et, clarify_message="Which record do you mean?")


def _mutation_sensitive_query(query: str) -> bool:
    import re

    text = (query or "").lower()
    return bool(
        re.search(r"\b(replace|supersede|record|update|delete|attach|remind)\b", text)
    )


def _resolve_document_entity(
    ctx: OpsContext,
    *,
    entity_id: str = "",
    query: str = "",
    pronoun: bool = False,
    session_context: dict[str, Any] | None = None,
    mutation_sensitive: bool = False,
) -> EntityResolution:
    from miya.services.intelligence.document_entity_linking import (
        document_resolution_to_entity,
        resolve_document_reference,
    )

    doc_ref = resolve_document_reference(
        ctx,
        document_id=entity_id,
        query=query,
        raw_message=query,
        session_context=session_context,
        pronoun=pronoun,
        mutation_sensitive=mutation_sensitive,
    )
    return document_resolution_to_entity(doc_ref)


def _resolve_task_db(
    ctx: OpsContext,
    *,
    query: str,
    pronoun: bool,
    session_context: dict[str, Any],
) -> EntityResolution:
    from miya.services.ops.tasks import find_tasks, get_task_state

    if pronoun and not query:
        tid = _from_working_memory_task(ctx) or _from_working_set(ctx, "tasks", session_context)
        if tid:
            return EntityResolution(entity_type="task", entity_id=tid, source="working_memory_or_set")
        return EntityResolution(
            entity_type="task",
            clarify_message="Which task do you mean? Tell me the title or short ref — I won't guess.",
        )

    if not query:
        return EntityResolution(
            entity_type="task",
            clarify_message="Which task? Give me the title or short ref.",
        )

    result = get_task_state(ctx, q=query, title=query)
    if result.success:
        task = (result.data or {}).get("task") or {}
        tid = str(task.get("id") or "")
        if tid:
            return EntityResolution(entity_type="task", entity_id=tid, source="database")
    if not result.success and result.code in ("task_wrong_establishment", "task_not_found"):
        return EntityResolution(
            entity_type="task",
            clarify_message=result.message_for_user or f"I couldn't find a task matching '{query}'.",
        )
    if result.needs_clarification:
        cands = (result.data or {}).get("candidates") or []
        msg = result.message_for_user or "Several tasks match — which one?"
        if "guess" not in msg.lower():
            msg = f"{msg.rstrip('.')} — which one? Reply with the title or #ref. I won't guess."
        return EntityResolution(
            entity_type="task",
            candidates=cands,
            clarify_message=msg,
        )

    listed = find_tasks(ctx, q=query, limit=5)
    tasks = (listed.data or {}).get("tasks") or [] if listed.success else []
    if len(tasks) == 1:
        return EntityResolution(
            entity_type="task",
            entity_id=str(tasks[0].get("id") or ""),
            source="database",
        )
    if len(tasks) > 1:
        return EntityResolution(
            entity_type="task",
            candidates=tasks,
            clarify_message="Several tasks match — which one? Reply with the title or #ref. I won't guess.",
        )
    return EntityResolution(
        entity_type="task",
        clarify_message=f"I couldn't find a task matching '{query}'.",
    )


def _resolve_incident_db(ctx: OpsContext, *, query: str) -> EntityResolution:
    from miya.services.ops.incidents import get_incident

    result = get_incident(ctx, q=query)
    if result.success:
        inc = (result.data or {}).get("incident") or {}
        return EntityResolution(
            entity_type="incident",
            entity_id=str(inc.get("id") or ""),
            source="database",
        )
    if result.needs_clarification:
        return EntityResolution(
            entity_type="incident",
            candidates=(result.data or {}).get("incidents") or [],
            clarify_message=result.message_for_user or "Which incident?",
        )
    return EntityResolution(
        entity_type="incident",
        clarify_message=result.message_for_user or "Which incident?",
    )


def _from_working_memory_task(ctx: OpsContext) -> str:
    try:
        from miya.services.intelligence.working_memory import get_working_memory

        wm = get_working_memory(user=ctx.user, restaurant=ctx.restaurant)
        return str(wm.get("current_task_id") or "")
    except Exception:
        return ""


def _from_working_set(ctx: OpsContext, kind: str, session_context: dict[str, Any]) -> str:
    try:
        from miya.services.working_set import get_working_set_entity

        return str(get_working_set_entity(ctx, kind=kind, session_context=session_context) or "")
    except Exception:
        ws = (session_context or {}).get("working_set") or {}
        ids = ws.get(kind) or ws.get(f"{kind}s") or []
        if isinstance(ids, list) and len(ids) == 1:
            return str(ids[0])
        return ""


def _from_working_set_index(
    ctx: OpsContext,
    kind: str,
    session_context: dict[str, Any],
    index: int,
) -> str:
    try:
        from miya.services.working_set import get_working_set_entity_at_index

        return str(
            get_working_set_entity_at_index(ctx, kind=kind, index=index, session_context=session_context)
            or ""
        )
    except Exception:
        ws = (session_context or {}).get("working_set") or {}
        ids = ws.get(kind) or ws.get(f"{kind}s") or []
        if isinstance(ids, list) and 0 <= index < len(ids):
            return str(ids[index])
        return ""


def parse_ordinal_reference(message: str) -> int | None:
    """Parse 'the second one' → 1 (0-based index)."""
    import re

    m = re.search(
        r"\b(?:the\s+)?(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th)\s+one\b",
        message or "",
        re.I,
    )
    if not m:
        return None
    word = m.group(1).lower()
    mapping = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "fourth": 3, "4th": 3, "fifth": 4, "5th": 4}
    return mapping.get(word)
