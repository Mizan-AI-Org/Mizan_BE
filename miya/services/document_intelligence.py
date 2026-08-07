"""Normalize OCR/parse output into structured operational fields for Miya."""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from django.utils.dateparse import parse_date


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        text = str(value).strip().replace(",", "").replace(" ", "")
        text = re.sub(r"[^\d.\-]", "", text)
        if not text or text in (".", "-", "-."):
            return None
        return Decimal(text)
    except (InvalidOperation, ValueError, TypeError):
        return None


def _to_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return parse_date(str(value).strip()[:32])


def extract_raw_fields(parse_metadata: dict[str, Any] | None) -> dict[str, Any]:
    meta = parse_metadata if isinstance(parse_metadata, dict) else {}
    fields = meta.get("fields")
    if isinstance(fields, dict) and fields:
        return dict(fields)
    structured = meta.get("structured")
    if isinstance(structured, dict) and structured:
        return dict(structured)
    return {}


def normalize_structured_fields(
    parse_metadata: dict[str, Any] | None = None,
    *,
    fields: dict[str, Any] | None = None,
    category: str = "",
    title: str = "",
    summary: str = "",
) -> dict[str, Any]:
    """
    Stable structured dict Miya must reason over (not raw OCR alone).
    """
    raw = dict(fields or {})
    if not raw:
        raw = extract_raw_fields(parse_metadata)

    vendor = str(
        raw.get("vendor") or raw.get("vendor_name") or raw.get("supplier") or ""
    ).strip()
    amount = _to_decimal(
        raw.get("amount") or raw.get("total") or raw.get("total_due") or raw.get("amount_due")
    )
    currency = str(raw.get("currency") or "").strip().upper()[:8]
    invoice_number = str(
        raw.get("invoice_number") or raw.get("reference_number") or ""
    ).strip()[:128]
    due_date = _to_date(raw.get("due_date"))
    issue_date = _to_date(raw.get("issue_date"))
    expiry_date = _to_date(
        raw.get("expiry_date") or raw.get("expires_at") or raw.get("expiration_date")
    )
    document_type = str(raw.get("document_type") or raw.get("type") or "").strip()
    ref = str(raw.get("reference_number") or raw.get("policy_number") or "").strip()[:128]

    cat = (category or (parse_metadata or {}).get("category") or "").strip()
    out: dict[str, Any] = {
        "category": cat,
        "title": (str(raw.get("title") or title or "").strip())[:255],
        "summary": (summary or (parse_metadata or {}).get("summary") or "")[:500],
        "vendor": vendor,
        "amount": str(amount) if amount is not None else None,
        "currency": currency or None,
        "invoice_number": invoice_number or None,
        "due_date": due_date.isoformat() if due_date else None,
        "issue_date": issue_date.isoformat() if issue_date else None,
        "expiry_date": expiry_date.isoformat() if expiry_date else None,
        "document_type": document_type or None,
        "reference_number": ref or None,
        "person_name": str(raw.get("person_name") or "").strip() or None,
        "confidence": float((parse_metadata or {}).get("confidence") or 0) or None,
    }
    return {k: v for k, v in out.items() if v not in (None, "", [])}


def structured_search_blob(structured: dict[str, Any], *, extra: str = "") -> str:
    parts = [str(v) for v in (structured or {}).values() if v]
    if extra:
        parts.append(extra)
    return " ".join(parts).lower()


def document_matches_query(
    *,
    title: str = "",
    summary: str = "",
    category: str = "",
    extracted_text: str = "",
    structured: dict[str, Any] | None = None,
    q: str = "",
) -> bool:
    needle = (q or "").strip().lower()
    if not needle:
        return True
    blob = " ".join(
        [
            title or "",
            summary or "",
            category or "",
            (extracted_text or "")[:4000],
            structured_search_blob(structured or {}),
        ]
    ).lower()
    if needle in blob:
        return True
    tokens = [t for t in re.split(r"\s+", needle) if len(t) >= 2]
    if not tokens:
        return False
    return all(t in blob for t in tokens)


def answer_from_structured(structured: dict[str, Any], *, intent: str = "") -> str:
    """Short factual answer from structured fields for common intents."""
    s = structured or {}
    intent_l = (intent or "").lower()
    if any(k in intent_l for k in ("expir", "expire", "renew", "assurance", "insurance")):
        if s.get("expiry_date"):
            return f"Expires on {s['expiry_date']}."
        return ""
    if any(k in intent_l for k in ("amount", "total", "how much", "montant")):
        if s.get("amount"):
            cur = s.get("currency") or ""
            return f"Amount: {s['amount']}{(' ' + cur) if cur else ''}."
        return ""
    if any(k in intent_l for k in ("supplier", "vendor", "fournisseur")):
        if s.get("vendor"):
            return f"Supplier/vendor: {s['vendor']}."
        return ""
    return ""


def merge_structured_into_metadata(
    parse_metadata: dict[str, Any] | None,
    structured: dict[str, Any],
) -> dict[str, Any]:
    meta = dict(parse_metadata or {})
    fields = meta.get("fields") if isinstance(meta.get("fields"), dict) else {}
    merged_fields = {**fields}
    if structured.get("vendor") and not merged_fields.get("vendor"):
        merged_fields["vendor"] = structured["vendor"]
    for key in ("amount", "currency", "invoice_number", "due_date", "issue_date", "expiry_date"):
        if structured.get(key) and not merged_fields.get(key):
            merged_fields[key] = structured[key]
    meta["fields"] = merged_fields
    meta["structured"] = structured
    return meta


def fields_json_for_extracted(structured: dict[str, Any]) -> str:
    if not structured:
        return ""
    try:
        return json.dumps(structured, ensure_ascii=False)
    except Exception:
        return str(structured)
