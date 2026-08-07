"""
Phase 14.2 — Unified DocumentInput ingestion (store + extract only).

UPLOAD ≠ MUTATION. Business records are created only through the Miya control plane.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from miya.models import TenantDocument

logger = logging.getLogger("miya.document_input")

# Explicit opt-in only — legacy callers must pass promote_linked_records=True.
PROMOTE_LINKED_RECORDS_DEFAULT = False


@dataclass
class DocumentInput:
    """Canonical representation of an uploaded document/image for Miya."""

    tenant_id: str
    establishment_id: str | None
    actor_id: str | None
    channel: str
    source: str  # WIDGET | WHATSAPP | ...
    filename: str
    mime_type: str
    caption: str = ""
    operation_id: str = ""
    correlation_id: str = ""
    # Post-ingest fields
    document_id: str = ""
    storage_path: str = ""
    file_url: str = ""
    document_type: str = ""
    extraction_status: str = "pending"  # pending | ok | failed | skipped
    structured_fields: dict[str, Any] = field(default_factory=dict)
    parse_metadata: dict[str, Any] = field(default_factory=dict)
    extracted_text: str = ""
    multimodal_context: dict[str, Any] | None = None
    idempotency_key: str = ""
    # Versioning (Phase 14.3.1)
    content_hash: str = ""
    document_family_id: str = ""
    version_number: int = 1
    is_current: bool = True
    supersedes_id: str = ""
    processing_status: str = "pending"
    is_duplicate: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "establishment_id": self.establishment_id,
            "actor_id": self.actor_id,
            "channel": self.channel,
            "source": self.source,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "caption": self.caption,
            "operation_id": self.operation_id,
            "correlation_id": self.correlation_id,
            "document_id": self.document_id,
            "storage_path": self.storage_path,
            "file_url": self.file_url,
            "document_type": self.document_type,
            "extraction_status": self.extraction_status,
            "structured_fields": dict(self.structured_fields),
            "parse_metadata": dict(self.parse_metadata),
            "multimodal_context": self.multimodal_context,
            "idempotency_key": self.idempotency_key,
            "content_hash": self.content_hash,
            "document_family_id": self.document_family_id,
            "version_number": self.version_number,
            "is_current": self.is_current,
            "supersedes_id": self.supersedes_id,
            "processing_status": self.processing_status,
            "is_duplicate": self.is_duplicate,
        }

    def to_session_patch(self) -> dict[str, Any]:
        """Patch for session_context after ingest."""
        patch: dict[str, Any] = {
            "attachment_ids": [self.document_id] if self.document_id else [],
            "_document_input": self.to_dict(),
        }
        if self.multimodal_context:
            patch["_multimodal"] = self.multimodal_context
        return patch


def _idempotency_key(
    *,
    tenant_id: str,
    actor_id: str | None,
    file_bytes: bytes,
    filename: str,
    operation_id: str,
) -> str:
    if operation_id:
        return f"doc-ingest:{operation_id}"
    digest = hashlib.sha256(file_bytes).hexdigest()[:24]
    return f"doc-ingest:{tenant_id}:{actor_id or 'anon'}:{digest}:{filename}"


def _find_existing_by_idempotency(
    restaurant_id: str,
    idempotency_key: str,
) -> TenantDocument | None:
    if not idempotency_key:
        return None
    try:
        return (
            TenantDocument.objects.filter(
                restaurant_id=restaurant_id,
                parse_metadata__idempotency_key=idempotency_key,
            )
            .order_by("-created_at")
            .first()
        )
    except Exception:
        return None


def _validate_tenant_actor(
    *,
    restaurant,
    uploaded_by,
    location_id: str | None,
) -> tuple[str | None, str | None]:
    """Return (establishment_id, error_code)."""
    tenant_id = str(getattr(restaurant, "id", "") or "")
    if not tenant_id:
        return None, "no_tenant"

    if uploaded_by is not None:
        user_restaurant_id = str(getattr(uploaded_by, "restaurant_id", "") or "")
        if user_restaurant_id and user_restaurant_id != tenant_id:
            return None, "tenant_mismatch"

        if location_id:
            try:
                from miya.services.ops.scoping import user_can_access_location

                if not user_can_access_location(uploaded_by, restaurant, str(location_id)):
                    return None, "establishment_forbidden"
            except Exception:
                logger.exception("establishment scope check failed")

    return (str(location_id) if location_id else None), None


def ingest_document(
    *,
    restaurant,
    uploaded_by=None,
    uploader_phone: str = "",
    source: str,
    file_bytes: bytes,
    filename: str,
    mime_type: str = "",
    caption: str = "",
    location_id: str | None = None,
    channel: str = "dashboard",
    operation_id: str | None = None,
    correlation_id: str | None = None,
    promote_linked_records: bool = PROMOTE_LINKED_RECORDS_DEFAULT,
    build_multimodal: bool = True,
    supersedes_document_id: str | None = None,
) -> DocumentInput:
    """
    Canonical ingestion: validate → store → extract → multimodal context.

    Does NOT mutate Invoice/Compliance/Incident unless promote_linked_records=True
    (explicit legacy opt-in only).
    """
    from miya.services.tenant_documents import serialize_tenant_document, store_tenant_document

    tenant_id = str(getattr(restaurant, "id", "") or "")
    actor_id = str(getattr(uploaded_by, "id", "") or "") if uploaded_by else None
    op_id = (operation_id or correlation_id or str(uuid.uuid4())).strip()
    corr_id = (correlation_id or op_id).strip()
    idem_key = _idempotency_key(
        tenant_id=tenant_id,
        actor_id=actor_id,
        file_bytes=file_bytes,
        filename=filename,
        operation_id=op_id,
    )

    est_id, err = _validate_tenant_actor(
        restaurant=restaurant,
        uploaded_by=uploaded_by,
        location_id=location_id,
    )
    if err:
        raise ValueError(err)

    existing = _find_existing_by_idempotency(tenant_id, idem_key)
    if existing is not None:
        return _document_input_from_doc(
            existing,
            channel=channel,
            operation_id=op_id,
            correlation_id=corr_id,
            idempotency_key=idem_key,
            build_multimodal=build_multimodal,
            restaurant_id=tenant_id,
            is_duplicate=True,
        )

    from miya.services.document_versioning import plan_version_create

    try:
        plan = plan_version_create(
            restaurant_id=tenant_id,
            file_bytes=file_bytes,
            supersedes_document_id=supersedes_document_id,
        )
    except ValueError as exc:
        if str(exc) == "supersedes_not_found":
            raise ValueError("supersedes_not_found") from exc
        raise

    if plan.reuse_existing_id:
        reused = TenantDocument.objects.filter(
            id=plan.reuse_existing_id,
            restaurant_id=tenant_id,
        ).first()
        if reused is not None:
            return _document_input_from_doc(
                reused,
                channel=channel,
                operation_id=op_id,
                correlation_id=corr_id,
                idempotency_key=idem_key,
                build_multimodal=build_multimodal,
                restaurant_id=tenant_id,
                is_duplicate=True,
            )

    if plan.demote_family_ids:
        from miya.services.document_versioning import demote_current_versions

        for family_id in plan.demote_family_ids:
            demote_current_versions(tenant_id, family_id)

    doc = store_tenant_document(
        restaurant=restaurant,
        uploaded_by=uploaded_by,
        uploader_phone=uploader_phone,
        source=source,
        file_bytes=file_bytes,
        filename=filename,
        mime_type=mime_type,
        caption=caption,
        location_id=est_id,
        promote_linked_records=promote_linked_records,
        idempotency_key=idem_key,
        operation_id=op_id,
        content_hash=plan.content_hash,
        document_family_id=str(plan.document_family_id) if plan.document_family_id else "",
        version_number=plan.version_number,
        is_current=plan.is_current,
        supersedes=plan.supersedes,
    )

    return _document_input_from_doc(
        doc,
        channel=channel,
        operation_id=op_id,
        correlation_id=corr_id,
        idempotency_key=idem_key,
        build_multimodal=build_multimodal,
        restaurant_id=tenant_id,
    )


def _document_input_from_doc(
    doc: TenantDocument,
    *,
    channel: str,
    operation_id: str,
    correlation_id: str,
    idempotency_key: str,
    build_multimodal: bool,
    restaurant_id: str,
    is_duplicate: bool = False,
) -> DocumentInput:
    from miya.services.document_versioning import serialize_version_meta
    from miya.services.tenant_documents import serialize_tenant_document

    row = serialize_tenant_document(doc)
    structured = row.get("structured") or row.get("fields") or {}
    parse_meta = doc.parse_metadata if isinstance(doc.parse_metadata, dict) else {}
    extraction_status = doc.processing_status or "pending"
    if extraction_status == "ok" or parse_meta.get("category"):
        extraction_status = extraction_status if doc.processing_status else "ok"
    if parse_meta.get("error"):
        extraction_status = "failed"

    version_meta = serialize_version_meta(doc)

    mm_ctx: dict[str, Any] | None = None
    if build_multimodal:
        from miya.services.intelligence.multimodal import build_multimodal_context

        mm = build_multimodal_context(
            user_message=doc.title or "",
            attachment_ids=[str(doc.id)],
            restaurant_id=restaurant_id,
        )
        mm_ctx = mm.to_dict()

    return DocumentInput(
        tenant_id=str(doc.restaurant_id),
        establishment_id=str(doc.location_id) if doc.location_id else None,
        actor_id=str(doc.uploaded_by_id) if doc.uploaded_by_id else None,
        channel=channel,
        source=doc.source or "",
        filename=doc.original_filename or "",
        mime_type=doc.mime_type or "",
        caption=doc.title or "",
        operation_id=operation_id,
        correlation_id=correlation_id,
        document_id=str(doc.id),
        storage_path=doc.storage_path or "",
        file_url=row.get("file_url") or doc.file_url or "",
        document_type=str(structured.get("document_type") or doc.category or ""),
        extraction_status=extraction_status,
        structured_fields=dict(structured) if isinstance(structured, dict) else {},
        parse_metadata=dict(parse_meta),
        extracted_text=(doc.extracted_text or "")[:500],
        multimodal_context=mm_ctx,
        idempotency_key=idempotency_key,
        content_hash=version_meta.get("content_hash") or "",
        document_family_id=version_meta.get("document_family_id") or "",
        version_number=int(version_meta.get("version_number") or 1),
        is_current=bool(version_meta.get("is_current")),
        supersedes_id=version_meta.get("supersedes_id") or "",
        processing_status=version_meta.get("processing_status") or extraction_status,
        is_duplicate=is_duplicate,
    )


def ingest_acknowledgement_message(doc_input: DocumentInput) -> str:
    """User-facing message after store+extract — no mutation claims."""
    structured = doc_input.structured_fields or {}
    expiry = structured.get("expiry_date") or doc_input.parse_metadata.get("expiry_date")
    doc_type = doc_input.document_type or "document"
    title = doc_input.caption or doc_input.filename or "your file"

    if expiry:
        return (
            f"Received {title}. I extracted an expiry date of {expiry}. "
            "Tell me if you'd like a reminder or have questions about this document."
        )
    if structured.get("vendor") and structured.get("amount"):
        return (
            f"Received {title}. I extracted vendor {structured.get('vendor')} "
            f"and amount {structured.get('amount')}. "
            "Say 'record this invoice' when you want me to add it to Mizan."
        )
    return f"Received {title}. I've stored and analyzed the file — how can I help with it?"
