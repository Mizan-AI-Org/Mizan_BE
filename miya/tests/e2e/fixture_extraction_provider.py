"""FIXTURE_PROVIDER — deterministic OCR/vision at external provider boundary only."""
from __future__ import annotations

import re
from typing import Any

PROVIDER_MODE = "FIXTURE_PROVIDER"


def _marker(blob: bytes) -> str | None:
    text = blob.decode("latin-1", errors="ignore")
    m = re.search(r"MIZAN_FIXTURE:([A-Z0-9_]+)", text)
    return m.group(1) if m else None


def _envelope(
    *,
    category: str,
    summary: str,
    fields: dict[str, Any],
    confidence: float = 0.93,
    suggested_action: str = "upload_document",
    error: str = "",
) -> dict[str, Any]:
    out = {
        "category": category,
        "confidence": confidence,
        "summary": summary,
        "suggested_action": suggested_action,
        "fields": fields,
        "extracted_kind": "fixture",
        "provider_mode": PROVIDER_MODE,
    }
    if error:
        out["error"] = error
    return out


def parse_document(blob: bytes, *, content_type: str = "", name: str = "") -> dict[str, Any]:
    key = _marker(blob)
    if key == "PROVIDER_ERROR":
        return _envelope(
            category="other",
            summary="Provider extraction failed.",
            fields={},
            confidence=0.0,
            error="provider_timeout",
        )
    if key == "INSURANCE_V1":
        return _envelope(
            category="id_or_certification",
            summary="Restaurant insurance certificate expiring 2026-09-30",
            fields={
                "document_type": "insurance",
                "insurer": "Atlas Assurance Maroc",
                "reference_number": "POL-INS-2026-001",
                "establishment": "Casablanca Kitchen Site",
                "issue_date": "2026-01-15",
                "expiry_date": "2026-09-30",
            },
        )
    if key == "INSURANCE_V2":
        return _envelope(
            category="id_or_certification",
            summary="Renewed restaurant insurance certificate expiring 2027-09-30",
            fields={
                "document_type": "insurance",
                "insurer": "Atlas Assurance Maroc",
                "reference_number": "POL-INS-2027-001",
                "establishment": "Casablanca Kitchen Site",
                "issue_date": "2027-01-01",
                "expiry_date": "2027-09-30",
            },
        )
    if key == "COMPLIANCE_V1":
        return _envelope(
            category="id_or_certification",
            summary="Food hygiene certificate expiring 2027-02-01",
            fields={
                "document_type": "hygiene_certificate",
                "certificate_type": "HACCP Level 2",
                "establishment": "Casablanca Kitchen Site",
                "reference_number": "HYG-2026-7788",
                "issue_date": "2026-02-01",
                "expiry_date": "2027-02-01",
            },
        )
    if key == "INVOICE_V1":
        return _envelope(
            category="invoice_or_receipt",
            summary="Invoice from Fresh Foods Casablanca for 2450 MAD",
            suggested_action="log_invoice",
            fields={
                "vendor": "Fresh Foods Casablanca",
                "invoice_number": "INV-1433-001",
                "invoice_date": "2026-06-15",
                "amount": "2450.00",
                "currency": "MAD",
                "establishment": "Casablanca Kitchen Site",
            },
        )
    if key == "ESTABLISHMENT_V1":
        return _envelope(
            category="id_or_certification",
            summary="Business registration extract for Casablanca Kitchen Site",
            fields={
                "document_type": "business_registration",
                "establishment": "Casablanca Kitchen Site",
                "reference_number": "RC-CASA-998877",
            },
        )
    return _envelope(
        category="other",
        summary=f"Unknown fixture document ({name or 'upload'})",
        fields={},
        confidence=0.0,
        error="unknown_fixture",
    )


def parse_photo(image_bytes: bytes, *, content_type: str = "image/jpeg") -> dict[str, Any]:
    key = _marker(image_bytes)
    if key == "IMAGE_INVOICE_V1":
        return _envelope(
            category="invoice_or_receipt",
            summary="Photo of supplier invoice from Fresh Foods Casablanca",
            suggested_action="log_invoice",
            fields={
                "vendor": "Fresh Foods Casablanca",
                "invoice_number": "INV-1433-PHOTO",
                "amount": "2450.00",
                "currency": "MAD",
            },
        )
    return _envelope(
        category="other",
        summary="Unknown fixture image",
        fields={},
        confidence=0.0,
        error="unknown_fixture",
    )
