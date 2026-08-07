"""
Phase 4 — Multimodal context for the shared Miya engine.

Voice / image / PDF / document all become structured context + text,
then enter the SAME planning → ops → verify path as chat.

OCR / vision extraction is INPUT to reasoning — never the final answer alone.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger("miya.intelligence.multimodal")

# Operational media kinds Miya reasons over
KIND_INCIDENT_PHOTO = "incident_photo"
KIND_INVOICE = "invoice"
KIND_RECEIPT = "receipt"
KIND_COMPLIANCE = "compliance"
KIND_EQUIPMENT = "equipment"
KIND_DOCUMENT = "document"
KIND_UNKNOWN = "unknown"


@dataclass
class AttachmentInsight:
    document_id: str
    title: str = ""
    category: str = ""
    mime_type: str = ""
    kind: str = KIND_UNKNOWN
    summary: str = ""
    vendor: str = ""
    amount: str = ""
    currency: str = ""
    invoice_number: str = ""
    expiry_date: str = ""
    invoice_id: str = ""
    compliance_document_id: str = ""
    structured: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    has_file: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MultimodalContext:
    """Server-built multimodal turn context — never invented by the LLM."""

    modalities: list[str] = field(default_factory=list)  # text, voice, image, pdf, document
    attachments: list[AttachmentInsight] = field(default_factory=list)
    primary_kind: str = KIND_UNKNOWN
    suggested_intent: str = ""
    suggested_entity: str = ""
    caption: str = ""
    reasoning_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "modalities": list(self.modalities),
            "attachments": [a.to_dict() for a in self.attachments],
            "primary_kind": self.primary_kind,
            "suggested_intent": self.suggested_intent or None,
            "suggested_entity": self.suggested_entity or None,
            "caption": self.caption,
            "reasoning_hint": self.reasoning_hint,
            "ocr_is_not_final_intelligence": True,
            "directive": (
                "OCR/vision fields are evidence for reasoning. "
                "Confirm operational actions via structured tools and database verify."
            ),
        }

    @property
    def primary(self) -> AttachmentInsight | None:
        return self.attachments[0] if self.attachments else None


def build_multimodal_context(
    *,
    user_message: str = "",
    attachment_ids: list[str] | None = None,
    restaurant_id: str | None = None,
    voice: bool = False,
) -> MultimodalContext:
    modalities: list[str] = []
    if (user_message or "").strip():
        modalities.append("text")
    if voice:
        modalities.append("voice")

    ctx = MultimodalContext(modalities=modalities, caption=(user_message or "").strip())
    ids = [str(i).strip() for i in (attachment_ids or []) if str(i).strip()]
    if not ids or not restaurant_id:
        ctx.reasoning_hint = _hint_for(ctx)
        return ctx

    try:
        from miya.services.tenant_documents import documents_for_ids, serialize_tenant_document

        docs = documents_for_ids(restaurant_id, ids)
    except Exception:
        logger.exception("multimodal document load failed")
        docs = []

    for doc in docs:
        try:
            row = serialize_tenant_document(doc, include_text=False)
        except Exception:
            continue
        insight = _insight_from_row(row)
        ctx.attachments.append(insight)
        mime = (insight.mime_type or "").lower()
        if mime.startswith("image/"):
            if "image" not in ctx.modalities:
                ctx.modalities.append("image")
            if "photo" not in ctx.modalities:
                ctx.modalities.append("photo")
        elif "pdf" in mime:
            if "pdf" not in ctx.modalities:
                ctx.modalities.append("pdf")
            if "document" not in ctx.modalities:
                ctx.modalities.append("document")
        else:
            if "document" not in ctx.modalities:
                ctx.modalities.append("document")

    if ctx.attachments:
        ctx.primary_kind = ctx.attachments[0].kind
        _suggest_ops(ctx)
    ctx.reasoning_hint = _hint_for(ctx)
    return ctx


def _insight_from_row(row: dict[str, Any]) -> AttachmentInsight:
    structured = row.get("structured") or row.get("fields") or {}
    if not isinstance(structured, dict):
        structured = {}
    category = str(row.get("category") or structured.get("category") or "").lower()
    tags = [str(t).lower() for t in (row.get("tags") or [])]
    title = str(row.get("title") or "")
    kind = _classify_kind(category, tags, title, structured, row.get("mime_type") or "")
    return AttachmentInsight(
        document_id=str(row.get("id") or ""),
        title=title,
        category=category,
        mime_type=str(row.get("mime_type") or ""),
        kind=kind,
        summary=str(row.get("summary") or "")[:400],
        vendor=str(row.get("vendor") or structured.get("vendor") or ""),
        amount=str(row.get("amount") or structured.get("amount") or ""),
        currency=str(row.get("currency") or structured.get("currency") or ""),
        invoice_number=str(row.get("invoice_number") or structured.get("invoice_number") or ""),
        expiry_date=str(row.get("expiry_date") or structured.get("expiry_date") or ""),
        invoice_id=str(row.get("invoice_id") or ""),
        compliance_document_id=str(row.get("compliance_document_id") or ""),
        structured=dict(structured),
        tags=tags,
        has_file=bool(row.get("has_file")),
    )


def _classify_kind(
    category: str,
    tags: list[str],
    title: str,
    structured: dict[str, Any],
    mime: str,
) -> str:
    blob = " ".join([category, title.lower(), " ".join(tags), str(structured.get("doc_type") or "")])
    if any(k in blob for k in ("invoice", "facture", "bill")) or structured.get("amount"):
        if "receipt" in blob or "reçu" in blob:
            return KIND_RECEIPT
        return KIND_INVOICE
    if any(k in blob for k in ("insurance", "compliance", "licence", "license", "permit", "expiry")):
        return KIND_COMPLIANCE
    if any(k in blob for k in ("incident", "accident", "safety", "hazard")):
        return KIND_INCIDENT_PHOTO
    if any(k in blob for k in ("equipment", "freezer", "frigo", "broken", "maintenance", "damage")):
        return KIND_EQUIPMENT
    if (mime or "").startswith("image/"):
        # Image without strong invoice/compliance signal → treat as operational photo
        return KIND_EQUIPMENT if any(k in blob for k in ("break", "leak", "fire")) else KIND_INCIDENT_PHOTO
    return KIND_DOCUMENT


def _suggest_ops(ctx: MultimodalContext) -> None:
    """Map media kind + caption → suggested planning intent (OCR is evidence only)."""
    primary = ctx.primary
    if not primary:
        return
    caption = (ctx.caption or "").lower()
    reportish = bool(
        re.search(r"\b(report|incident|log|create|broken|freezer|frigo|accident)\b", caption)
        or caption.strip() in ("", "this", "report this", "report this.", "ça", "ca")
    )

    if primary.kind in (KIND_INCIDENT_PHOTO, KIND_EQUIPMENT) and (
        reportish or not caption or "report" in caption
    ):
        ctx.suggested_intent = "CREATE"
        ctx.suggested_entity = "incident"
        return
    if primary.kind in (KIND_INVOICE, KIND_RECEIPT):
        ctx.suggested_intent = "CREATE"
        ctx.suggested_entity = "invoice"
        return
    if primary.kind == KIND_COMPLIANCE or (
        primary.expiry_date and "insurance" in (primary.title + primary.category).lower()
    ):
        ctx.suggested_intent = "REMIND"
        ctx.suggested_entity = "reminder"
        return
    if primary.kind == KIND_DOCUMENT:
        ctx.suggested_intent = "RETRIEVE"
        ctx.suggested_entity = "document"


def _hint_for(ctx: MultimodalContext) -> str:
    if not ctx.attachments:
        if "voice" in ctx.modalities:
            return "Voice transcript entered the shared Miya engine as text."
        return ""
    p = ctx.primary
    assert p is not None
    bits = [
        f"Attached {p.kind} document_id={p.document_id}",
        f"category={p.category or 'n/a'}",
    ]
    if p.vendor:
        bits.append(f"vendor={p.vendor}")
    if p.amount:
        bits.append(f"amount={p.amount}")
    if p.expiry_date:
        bits.append(f"expiry={p.expiry_date}")
    bits.append("Reason over these fields; verify with DB tools before claiming Done.")
    return "; ".join(bits)


def read_document_bytes(document_id: str, restaurant_id: str) -> tuple[bytes, str, str] | None:
    """Load file bytes for attach-to-incident etc."""
    try:
        from miya.models import TenantDocument

        doc = TenantDocument.objects.filter(id=document_id, restaurant_id=restaurant_id).first()
        if not doc or not doc.file:
            return None
        with doc.file.open("rb") as fh:
            data = fh.read()
        if not data:
            return None
        name = doc.original_filename or getattr(doc.file, "name", "") or "upload.bin"
        mime = doc.mime_type or "application/octet-stream"
        return bytes(data), mime, name
    except Exception:
        logger.exception("read_document_bytes failed id=%s", document_id)
        return None
