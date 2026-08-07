"""Phase 14.3.1 — TenantDocument version chain helpers (immutable rows)."""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.db.models import Max

from miya.models import TenantDocument


def compute_content_hash(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes or b"").hexdigest()


def find_by_content_hash(
    restaurant_id: str,
    content_hash: str,
    *,
    document_family_id: str | None = None,
) -> TenantDocument | None:
    """Return an existing row with identical bytes (tenant-scoped)."""
    if not content_hash:
        return None
    qs = TenantDocument.objects.filter(
        restaurant_id=restaurant_id,
        content_hash=content_hash,
    )
    if document_family_id:
        qs = qs.filter(document_family_id=document_family_id)
    return qs.order_by("-version_number", "-created_at").first()


def resolve_current_version(doc: TenantDocument) -> TenantDocument:
    """Return the current head for this document's family."""
    if doc.is_current:
        return doc
    family_id = doc.document_family_id or doc.id
    current = (
        TenantDocument.objects.filter(
            restaurant_id=doc.restaurant_id,
            document_family_id=family_id,
            is_current=True,
        )
        .order_by("-version_number", "-created_at")
        .first()
    )
    return current or doc


def _valid_restaurant_id(restaurant_id: str) -> bool:
    try:
        uuid.UUID(str(restaurant_id))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def get_document_versions(
    restaurant_id: str,
    document_family_id: str,
) -> list[TenantDocument]:
    if not document_family_id or not _valid_restaurant_id(restaurant_id):
        return []
    return list(
        TenantDocument.objects.filter(
            restaurant_id=restaurant_id,
            document_family_id=document_family_id,
        ).order_by("version_number", "created_at")
    )


def _processing_status_from_parse(parse_result: dict[str, Any]) -> str:
    if parse_result.get("error"):
        return "failed"
    if parse_result.get("category"):
        return "ok"
    return "ok"


@dataclass
class VersionCreatePlan:
    content_hash: str
    document_family_id: uuid.UUID | None
    version_number: int
    supersedes: TenantDocument | None
    is_current: bool
    demote_family_ids: list[uuid.UUID]
    reuse_existing_id: str | None = None


def plan_version_create(
    *,
    restaurant_id: str,
    file_bytes: bytes,
    supersedes_document_id: str | None = None,
) -> VersionCreatePlan:
    """
    Decide whether ingest creates v1, a new version, or should dedupe by hash.

    Caller handles idempotency_key replay before invoking this.
    """
    content_hash = compute_content_hash(file_bytes)
    supersede_id = (supersedes_document_id or "").strip()

    if supersede_id:
        superseded = TenantDocument.objects.filter(
            id=supersede_id,
            restaurant_id=restaurant_id,
        ).first()
        if superseded is None:
            raise ValueError("supersedes_not_found")

        family_id = superseded.document_family_id or superseded.id
        same_bytes = find_by_content_hash(
            restaurant_id,
            content_hash,
            document_family_id=str(family_id),
        )
        if same_bytes is not None:
            return VersionCreatePlan(
                content_hash=content_hash,
                document_family_id=family_id,
                version_number=same_bytes.version_number,
                supersedes=superseded,
                is_current=same_bytes.is_current,
                demote_family_ids=[],
                reuse_existing_id=str(same_bytes.id),
            )

        max_ver = (
            TenantDocument.objects.filter(
                restaurant_id=restaurant_id,
                document_family_id=family_id,
            ).aggregate(m=Max("version_number"))["m"]
            or 0
        )
        return VersionCreatePlan(
            content_hash=content_hash,
            document_family_id=family_id,
            version_number=int(max_ver) + 1,
            supersedes=superseded,
            is_current=True,
            demote_family_ids=[family_id],
        )

    existing = find_by_content_hash(restaurant_id, content_hash)
    if existing is not None:
        return VersionCreatePlan(
            content_hash=content_hash,
            document_family_id=existing.document_family_id or existing.id,
            version_number=existing.version_number,
            supersedes=None,
            is_current=existing.is_current,
            demote_family_ids=[],
            reuse_existing_id=str(existing.id),
        )

    return VersionCreatePlan(
        content_hash=content_hash,
        document_family_id=None,
        version_number=1,
        supersedes=None,
        is_current=True,
        demote_family_ids=[],
    )


@transaction.atomic
def demote_current_versions(restaurant_id: str, document_family_id: uuid.UUID) -> None:
    TenantDocument.objects.filter(
        restaurant_id=restaurant_id,
        document_family_id=document_family_id,
        is_current=True,
    ).update(is_current=False)


def ensure_document_family_id(doc: TenantDocument) -> TenantDocument:
    if doc.document_family_id:
        return doc
    doc.document_family_id = doc.id
    doc.save(update_fields=["document_family_id", "updated_at"])
    return doc


def serialize_version_meta(doc: TenantDocument) -> dict[str, Any]:
    return {
        "content_hash": doc.content_hash or "",
        "document_family_id": str(doc.document_family_id) if doc.document_family_id else None,
        "version_number": int(doc.version_number or 1),
        "is_current": bool(doc.is_current),
        "supersedes_id": str(doc.supersedes_id) if doc.supersedes_id else None,
        "processing_status": doc.processing_status or "pending",
        "uploaded_at": doc.created_at.isoformat() if doc.created_at else None,
        "uploaded_by_id": str(doc.uploaded_by_id) if doc.uploaded_by_id else None,
    }
