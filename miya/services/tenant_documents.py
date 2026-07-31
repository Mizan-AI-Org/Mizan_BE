"""Store, parse, and recall tenant documents for Miya."""

from __future__ import annotations

import json
import logging
from typing import Any

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from core.s3_storage import file_field_download_url
from miya.models import TenantDocument

logger = logging.getLogger(__name__)

MAX_EXTRACT_CHARS = 12_000
MAX_SUMMARY_CHARS = 2_000
ALLOWED_MIME_PREFIXES = ("image/", "application/pdf", "text/")
ALLOWED_MIME_EXACT = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/msword",
    "application/vnd.ms-excel",
    "application/csv",
    "text/csv",
    "text/plain",
}
MAX_UPLOAD_BYTES = 12 * 1024 * 1024


def _mime_allowed(mime_type: str) -> bool:
    ct = (mime_type or "").split(";")[0].strip().lower()
    if not ct:
        return True
    if ct in ALLOWED_MIME_EXACT:
        return True
    return any(ct.startswith(p) for p in ALLOWED_MIME_PREFIXES)


def _parse_upload(blob: bytes, *, mime_type: str, filename: str) -> dict[str, Any]:
    ct = (mime_type or "").lower()
    try:
        if ct.startswith("image/"):
            from scheduling.photo_router_service import parse_photo

            return parse_photo(blob, content_type=mime_type or "image/jpeg")
        from scheduling.document_router_service import parse_document

        return parse_document(blob, content_type=mime_type, name=filename)
    except Exception as exc:
        logger.warning("tenant document parse failed: %s", exc)
        return {
            "category": "other",
            "confidence": 0.0,
            "summary": "Uploaded file stored; automatic parsing was unavailable.",
            "fields": {},
        }


def _build_title(filename: str, parse_result: dict[str, Any]) -> str:
    fields = parse_result.get("fields") if isinstance(parse_result.get("fields"), dict) else {}
    for key in ("title", "document_title", "vendor", "employee_name", "name"):
        val = fields.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()[:255]
    summary = (parse_result.get("summary") or "").strip()
    if summary and len(summary) <= 120:
        return summary
    base = (filename or "Uploaded document").rsplit("/", 1)[-1]
    return base[:255] or "Uploaded document"


def serialize_tenant_document(doc: TenantDocument, *, include_text: bool = False) -> dict[str, Any]:
    href = ""
    if doc.file:
        try:
            href = file_field_download_url(doc.file) or doc.file.url or ""
        except Exception:
            href = doc.file_url or ""
    elif doc.file_url:
        href = doc.file_url

    payload: dict[str, Any] = {
        "id": str(doc.id),
        "title": doc.title,
        "category": doc.category,
        "summary": doc.summary,
        "original_filename": doc.original_filename,
        "mime_type": doc.mime_type,
        "source": doc.source,
        "file_url": href,
        "uploaded_by": (
            f"{doc.uploaded_by.first_name} {doc.uploaded_by.last_name}".strip()
            if doc.uploaded_by
            else doc.uploader_phone or "unknown"
        ),
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "compliance_document_id": (
            str(doc.compliance_document_id) if doc.compliance_document_id else None
        ),
        "tags": doc.tags or [],
    }
    if include_text and doc.extracted_text:
        payload["extracted_text"] = doc.extracted_text[:MAX_EXTRACT_CHARS]
    return payload


def store_tenant_document(
    *,
    restaurant,
    uploaded_by=None,
    uploader_phone: str = "",
    source: str,
    file_bytes: bytes,
    filename: str,
    mime_type: str = "",
    caption: str = "",
) -> TenantDocument:
    if not file_bytes:
        raise ValueError("empty_file")
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise ValueError("file_too_large")
    if mime_type and not _mime_allowed(mime_type):
        raise ValueError("unsupported_type")

    parse_result = _parse_upload(file_bytes, mime_type=mime_type, filename=filename)
    title = (caption or "").strip() or _build_title(filename, parse_result)
    summary = (parse_result.get("summary") or "").strip()[:MAX_SUMMARY_CHARS]
    category = (parse_result.get("category") or "other").strip()[:64]

    extracted = ""
    if not (mime_type or "").lower().startswith("image/"):
        try:
            from scheduling.document_router_service import extract_document_text

            _, raw_text = extract_document_text(file_bytes, content_type=mime_type, name=filename)
            extracted = (raw_text or "").strip()[:MAX_EXTRACT_CHARS]
        except Exception:
            extracted = ""
    fields = parse_result.get("fields") if isinstance(parse_result.get("fields"), dict) else {}
    if not extracted and fields:
        extracted = json.dumps(fields, ensure_ascii=False)[:MAX_EXTRACT_CHARS]

    doc = TenantDocument(
        restaurant=restaurant,
        uploaded_by=uploaded_by,
        uploader_phone=(uploader_phone or "")[:32],
        source=source,
        title=title[:255],
        original_filename=(filename or "")[:255],
        mime_type=(mime_type or "")[:128],
        category=category,
        summary=summary,
        extracted_text=extracted,
        parse_metadata=parse_result if isinstance(parse_result, dict) else {},
    )
    safe_name = (filename or "upload.bin").rsplit("/", 1)[-1]
    doc.file.save(safe_name, ContentFile(file_bytes), save=True)
    try:
        doc.file_url = doc.file.url or ""
        doc.storage_path = doc.file.name or ""
        doc.save(update_fields=["file_url", "storage_path", "updated_at"])
    except Exception:
        pass
    return doc


def documents_for_ids(restaurant_id, doc_ids: list[str]) -> list[TenantDocument]:
    if not doc_ids:
        return []
    return list(
        TenantDocument.objects.filter(
            restaurant_id=restaurant_id,
            id__in=doc_ids,
        ).order_by("-created_at")[:10]
    )


def attachment_context_block(docs: list[TenantDocument]) -> str:
    if not docs:
        return ""
    lines = ["[ATTACHED DOCUMENTS — authoritative for this turn]"]
    for doc in docs:
        row = serialize_tenant_document(doc, include_text=True)
        lines.append(
            f"• {row['title']} (document_id={row['id']}, category={row['category']}): "
            f"{row['summary'] or 'No summary yet.'}"
        )
        if row.get("extracted_text"):
            lines.append(f"  Details: {row['extracted_text'][:800]}")
    return "\n".join(lines) + "\n"


def recent_documents_block(restaurant, *, limit: int = 12) -> str:
    docs = list(
        TenantDocument.objects.filter(restaurant=restaurant).order_by("-created_at")[:limit]
    )
    if not docs:
        return "\n[TENANT DOCUMENTS] None uploaded yet. Managers and staff can attach files in the Miya widget or WhatsApp.\n"
    lines = ["\n[TENANT DOCUMENTS — Miya remembers these uploads]"]
    for doc in docs:
        row = serialize_tenant_document(doc)
        lines.append(
            f"  • {row['title']} (document_id={row['id']}, {row['category']}): "
            f"{row['summary'] or 'stored file'}"
        )
    return "\n".join(lines) + "\n"
