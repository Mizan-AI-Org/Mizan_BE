"""Idempotency for messages and mutations."""
from __future__ import annotations

from miya.services.message_pipeline import claim_mutation_once, new_operation_id


def claim_message_once(message_id: str, *, ttl_seconds: int = 600) -> bool:
    """
    First claim of this inbound message_id wins.
    Returns False if the message was already processed.
    """
    mid = (message_id or "").strip()
    if not mid:
        return True
    return claim_mutation_once(f"message:{mid}", ttl_seconds=ttl_seconds)


def claim_operation_once(operation_id: str, *, ttl_seconds: int = 120) -> bool:
    """First claim of this operation_id may mutate; duplicates are suppressed."""
    return claim_mutation_once(operation_id, ttl_seconds=ttl_seconds)


def ensure_operation_id(
    operation: str,
    arguments: dict | None,
    *,
    message_id: str = "",
    provided: str = "",
) -> str:
    oid = (provided or "").strip()
    if oid:
        return oid
    return new_operation_id(operation, arguments or {}, message_id=message_id)
