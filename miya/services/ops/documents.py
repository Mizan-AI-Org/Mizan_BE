"""Canonical document intelligence — find / get / show / query structured fields."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.db.models import Q
from django.utils import timezone

from miya.services.document_intelligence import (
    answer_from_structured,
    document_matches_query,
    normalize_structured_fields,
)
from miya.services.ops.context import OpsContext, require_permission, require_restaurant
from miya.services.ops.result import OpsResult, fail, ok


def _since_bounds(since: str = "", days: int | None = None):
    """Return (start_dt, end_dt_exclusive) or (start, None)."""
    from datetime import datetime as dt

    s = (since or "").strip().lower()
    if s in ("yesterday", "hier"):
        day = timezone.localdate() - timedelta(days=1)
        start = timezone.make_aware(dt.combine(day, dt.min.time()))
        end = start + timedelta(days=1)
        return start, end
    if s in ("today", "aujourd'hui", "aujourdhui"):
        day = timezone.localdate()
        start = timezone.make_aware(dt.combine(day, dt.min.time()))
        end = start + timedelta(days=1)
        return start, end
    if days is not None:
        return timezone.now() - timedelta(days=max(0, int(days))), None
    return None, None


def _serialize_compliance(doc) -> dict[str, Any]:
    from payroll.services.compliance_documents import serialize_document

    row = serialize_document(doc)
    row["kind"] = "compliance"
    structured = {
        "document_type": getattr(doc, "document_type", None) or row.get("document_type"),
        "title": doc.title,
        "expiry_date": doc.expires_at.isoformat() if getattr(doc, "expires_at", None) else None,
        "reference_number": getattr(doc, "reference_number", None) or None,
        "category": "compliance",
    }
    row["structured"] = {k: v for k, v in structured.items() if v}
    row["fields"] = row["structured"]
    row["expiry_date"] = structured.get("expiry_date")
    row["has_file"] = bool(getattr(doc, "file", None) and getattr(doc.file, "name", None))
    try:
        from core.s3_storage import file_field_download_url

        if doc.file and doc.file.name:
            row["file_url"] = file_field_download_url(doc.file) or row.get("file_url") or ""
    except Exception:
        pass
    return row


def find_documents(
    ctx: OpsContext,
    *,
    q: str = "",
    kind: str = "",
    category: str = "",
    since: str = "",
    days: int | None = None,
    limit: int = 20,
) -> OpsResult:
    err = require_restaurant(ctx)
    if err:
        return err

    from miya.services.ops.context import require_establishment_context
    from miya.services.ops.scoping import apply_location_scope, filter_visible_location_ids

    est_err = require_establishment_context(ctx, for_action="documents")
    if est_err:
        return est_err

    needle = (q or "").strip()
    kind_l = (kind or "").strip().lower()
    cat = (category or "").strip()
    start, end = _since_bounds(since, days)
    lim = max(1, min(int(limit or 20), 40))
    rows: list[dict[str, Any]] = []

    want_compliance = kind_l in ("", "all", "compliance")
    want_tenant = kind_l in ("", "all", "tenant", "tenant_file", "file", "upload")
    want_invoice_docs = kind_l in ("", "all", "invoice", "invoices")

    if want_compliance:
        try:
            from payroll.models import ComplianceDocument

            cqs = ComplianceDocument.objects.filter(
                restaurant=ctx.restaurant,
                status=ComplianceDocument.STATUS_ACTIVE,
            )
            if needle:
                insurance_q = Q()
                if "insur" in needle.lower() or "assurance" in needle.lower():
                    insurance_q = (
                        Q(document_type__iexact="INSURANCE")
                        | Q(title__icontains="insur")
                        | Q(title__icontains="assurance")
                    )
                cqs = cqs.filter(
                    insurance_q
                    | Q(title__icontains=needle)
                    | Q(document_type__icontains=needle)
                    | Q(description__icontains=needle)
                    | Q(reference_number__icontains=needle)
                )
            for doc in cqs.order_by("expires_at", "title")[:lim]:
                rows.append(_serialize_compliance(doc))
        except Exception:
            pass

    if want_tenant or want_invoice_docs:
        try:
            from miya.models import TenantDocument
            from miya.services.tenant_documents import serialize_tenant_document

            tqs = TenantDocument.objects.filter(
                restaurant_id=ctx.restaurant_id,
                is_current=True,
            )
            if ctx.location_id:
                tqs = apply_location_scope(tqs, location_id=ctx.location_id, field="location_id")
            elif len(ctx.available_locations) > 1:
                tqs = filter_visible_location_ids(
                    tqs,
                    location_ids=[r["id"] for r in ctx.available_locations],
                    field="location_id",
                )
            if start is not None:
                tqs = tqs.filter(created_at__gte=start)
            if end is not None:
                tqs = tqs.filter(created_at__lt=end)
            if cat:
                tqs = tqs.filter(category__icontains=cat)
            if kind_l in ("invoice", "invoices"):
                tqs = tqs.filter(Q(category__icontains="invoice") | ~Q(vendor_name=""))
            # Prefetch then filter in Python for structured/extracted search
            for doc in tqs.order_by("-created_at")[: lim * 4]:
                structured = getattr(doc, "structured_fields", None) or normalize_structured_fields(
                    getattr(doc, "parse_metadata", None),
                    category=doc.category,
                    title=doc.title,
                    summary=doc.summary,
                )
                if not document_matches_query(
                    title=doc.title or "",
                    summary=doc.summary or "",
                    category=doc.category or "",
                    extracted_text=doc.extracted_text or "",
                    structured=structured,
                    q=needle,
                ):
                    continue
                row = serialize_tenant_document(doc)
                row["kind"] = "tenant_file"
                rows.append(row)
                if len(rows) >= lim * 2:
                    break
        except Exception:
            pass

    # Also surface finance invoices for invoice queries / yesterday uploads
    if want_invoice_docs or (
        needle and any(k in needle.lower() for k in ("invoice", "facture", "supplier", "vendor", "amount"))
    ):
        try:
            from finance.models import Invoice

            iqs = Invoice.objects.filter(restaurant=ctx.restaurant)
            if ctx.location_id:
                iqs = apply_location_scope(iqs, location_id=ctx.location_id, field="location_id")
            elif len(ctx.available_locations) > 1:
                iqs = filter_visible_location_ids(
                    iqs,
                    location_ids=[r["id"] for r in ctx.available_locations],
                    field="location_id",
                )
            if start is not None:
                iqs = iqs.filter(created_at__gte=start)
            if end is not None:
                iqs = iqs.filter(created_at__lt=end)
            if needle:
                iqs = iqs.filter(
                    Q(vendor_name__icontains=needle)
                    | Q(invoice_number__icontains=needle)
                    | Q(notes__icontains=needle)
                )
            for inv in iqs.order_by("-created_at")[:lim]:
                structured = {
                    "vendor": inv.vendor_name,
                    "amount": str(inv.amount) if inv.amount is not None else None,
                    "currency": inv.currency,
                    "invoice_number": inv.invoice_number or None,
                    "due_date": inv.due_date.isoformat() if inv.due_date else None,
                    "issue_date": inv.issue_date.isoformat() if inv.issue_date else None,
                    "category": "invoice_or_receipt",
                }
                ocr = getattr(inv, "ocr_fields", None) or {}
                if isinstance(ocr, dict):
                    structured = {**{k: v for k, v in ocr.items() if v}, **{k: v for k, v in structured.items() if v}}
                rows.append(
                    {
                        "id": str(inv.id),
                        "kind": "invoice",
                        "title": f"Invoice — {inv.vendor_name}",
                        "status": inv.status,
                        "vendor": inv.vendor_name,
                        "amount": str(inv.amount) if inv.amount is not None else None,
                        "currency": inv.currency,
                        "invoice_number": inv.invoice_number,
                        "due_date": inv.due_date.isoformat() if inv.due_date else None,
                        "created_at": inv.created_at.isoformat() if inv.created_at else None,
                        "structured": structured,
                        "fields": structured,
                        "file_url": getattr(inv, "photo_url", None) or "",
                    }
                )
        except Exception:
            pass

    # Deduplicate by kind+id
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for r in rows:
        key = f"{r.get('kind')}:{r.get('id')}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    deduped = deduped[:lim]

    if not deduped:
        return fail(
            code="documents_not_found",
            message="No documents match that search.",
            data={"documents": [], "count": 0},
        )

    # Build message highlighting structured facts when single hit
    msg = f"Found {len(deduped)} document(s)."
    if len(deduped) == 1:
        one = deduped[0]
        s = one.get("structured") or one.get("fields") or {}
        bits = []
        if s.get("expiry_date") or one.get("expiry_date"):
            bits.append(f"expires {s.get('expiry_date') or one.get('expiry_date')}")
        if s.get("amount") or one.get("amount"):
            bits.append(f"amount {s.get('amount') or one.get('amount')}")
        if s.get("vendor") or one.get("vendor"):
            bits.append(f"vendor {s.get('vendor') or one.get('vendor')}")
        if bits:
            msg = f"Found {one.get('title') or 'document'} — " + ", ".join(bits) + "."

    return ok(
        message=msg,
        verified=True,
        data={"documents": deduped, "count": len(deduped)},
        miya_directive=(
            "Answer using structured/fields (vendor, amount, expiry_date) — "
            "never invent values. If the user wants the file, call show_document."
        ),
    )


def get_document(
    ctx: OpsContext,
    *,
    document_id: str = "",
    q: str = "",
    kind: str = "",
) -> OpsResult:
    err = require_restaurant(ctx)
    if err:
        return err

    did = (document_id or "").strip()
    if did:
        # Tenant
        try:
            from miya.models import TenantDocument
            from miya.services.tenant_documents import serialize_tenant_document

            doc = TenantDocument.objects.filter(restaurant_id=ctx.restaurant_id, id=did).first()
            if doc:
                from miya.services.ops.context import guard_entity_location

                loc_err = guard_entity_location(ctx, doc)
                if loc_err:
                    return loc_err
                row = serialize_tenant_document(doc, include_text=True)
                row["kind"] = "tenant_file"
                from miya.services.document_versioning import (
                    get_document_versions,
                    resolve_current_version,
                )

                current = resolve_current_version(doc)
                if str(current.id) != str(doc.id):
                    row["current_version_id"] = str(current.id)
                family_id = doc.document_family_id or doc.id
                versions = get_document_versions(str(ctx.restaurant_id), str(family_id))
                if len(versions) > 1:
                    row["version_history"] = [
                        {
                            "id": str(v.id),
                            "version_number": v.version_number,
                            "is_current": v.is_current,
                            "uploaded_at": v.created_at.isoformat() if v.created_at else None,
                            "content_hash": v.content_hash,
                        }
                        for v in versions
                    ]
                return ok(
                    message=_detail_message(row),
                    verified=True,
                    data={"document": row, "documents": [row], "structured": row.get("structured")},
                    miya_directive="Use structured fields for facts. Do not invent OCR values.",
                )
        except Exception:
            pass
        # Compliance
        try:
            from payroll.models import ComplianceDocument

            doc = ComplianceDocument.objects.filter(restaurant=ctx.restaurant, id=did).first()
            if doc:
                row = _serialize_compliance(doc)
                return ok(
                    message=_detail_message(row),
                    verified=True,
                    data={"document": row, "documents": [row], "structured": row.get("structured")},
                )
        except Exception:
            pass
        # Invoice
        try:
            from finance.models import Invoice

            inv = Invoice.objects.filter(restaurant=ctx.restaurant, id=did).first()
            if inv:
                from miya.services.ops.context import guard_entity_location

                loc_err = guard_entity_location(ctx, inv)
                if loc_err:
                    return loc_err
                found = find_documents(ctx, q=str(inv.vendor_name or ""), kind="invoice", limit=5)
                for r in (found.data or {}).get("documents") or []:
                    if str(r.get("id")) == str(inv.id):
                        return ok(
                            message=_detail_message(r),
                            verified=True,
                            data={"document": r, "documents": [r], "structured": r.get("structured")},
                        )
        except Exception:
            pass
        return fail(code="document_not_found", message="I couldn't find that document.")

    if q:
        found = find_documents(ctx, q=q, kind=kind, limit=5)
        if not found.success:
            return found
        docs = (found.data or {}).get("documents") or []
        if len(docs) == 1:
            return get_document(ctx, document_id=str(docs[0].get("id") or ""), kind=docs[0].get("kind") or "")
        if len(docs) > 1:
            return fail(
                code="needs_clarification",
                message="Several documents match — which one?",
                needs_clarification=True,
                data={"documents": docs},
            )
    return fail(code="document_required", message="Give me a document id or a clearer name.")


def _detail_message(row: dict[str, Any]) -> str:
    s = row.get("structured") or row.get("fields") or {}
    parts = [row.get("title") or "Document"]
    if s.get("expiry_date") or row.get("expiry_date"):
        parts.append(f"expires {s.get('expiry_date') or row.get('expiry_date')}")
    if s.get("vendor") or row.get("vendor"):
        parts.append(f"vendor {s.get('vendor') or row.get('vendor')}")
    if s.get("amount") or row.get("amount"):
        cur = s.get("currency") or row.get("currency") or ""
        parts.append(f"amount {s.get('amount') or row.get('amount')}{(' ' + cur) if cur else ''}")
    return " — ".join(parts) + "."


def show_document(
    ctx: OpsContext,
    *,
    document_id: str = "",
    q: str = "",
    phone: str = "",
) -> OpsResult:
    """Return secure file reference; on WhatsApp attempt to send the file."""
    detail = get_document(ctx, document_id=document_id, q=q)
    if not detail.success:
        return detail
    row = (detail.data or {}).get("document") or {}
    file_url = row.get("file_url") or ""
    storage_key = ""
    whatsapp_sent = False
    send_error = ""

    # Try load bytes from tenant document file field
    if ctx.channel == "whatsapp" and row.get("kind") == "tenant_file" and row.get("id"):
        try:
            from miya.models import TenantDocument

            doc = TenantDocument.objects.filter(restaurant_id=ctx.restaurant_id, id=row["id"]).first()
            to_phone = (phone or getattr(ctx.user, "phone", None) or "").strip()
            if doc and doc.file and doc.file.name and to_phone:
                storage_key = doc.file.name
                doc.file.open("rb")
                try:
                    data = doc.file.read()
                finally:
                    doc.file.close()
                if data:
                    from notifications.services import notification_service

                    mime = doc.mime_type or "application/octet-stream"
                    ok_send, meta = notification_service.send_whatsapp_media_attachment(
                        to_phone,
                        file_bytes=data,
                        mime_type=mime,
                        filename=doc.original_filename or "document.pdf",
                        caption=(doc.title or "Document")[:900],
                        as_document=not mime.startswith("image/"),
                    )
                    whatsapp_sent = bool(ok_send)
                    if not ok_send:
                        send_error = str((meta or {}).get("error") or "send_failed")
        except Exception as exc:
            send_error = str(exc)

    if whatsapp_sent:
        msg = f"Sent {row.get('title') or 'the document'} on WhatsApp."
    elif file_url:
        msg = (
            f"{row.get('title') or 'Document'} is on file. "
            "Open Documents in Settings / Miya uploads on the dashboard to view it."
        )
    else:
        msg = f"{row.get('title') or 'Document'} is tracked but has no downloadable file yet."

    return ok(
        message=msg,
        verified=True,
        data={
            "document": row,
            "file_url": file_url,
            "storage_key": storage_key,
            "secure_document_ref": {
                "document_id": row.get("id"),
                "kind": row.get("kind"),
                "title": row.get("title"),
                "storage_key": storage_key,
                "has_secure_url": bool(file_url),
            },
            "structured": row.get("structured") or row.get("fields"),
            "whatsapp_file_sent": whatsapp_sent,
            "whatsapp_send_error": send_error or None,
        },
        miya_directive=(
            "If whatsapp_file_sent, say you sent the file. "
            "Otherwise point to Documents on the dashboard. Never paste raw URLs."
        ),
    )


def query_document_intelligence(
    ctx: OpsContext,
    *,
    q: str = "",
    question: str = "",
    document_id: str = "",
    since: str = "",
    days: int | None = None,
) -> OpsResult:
    """
    Answer operational questions from structured fields:
    insurance expiry, invoice amount/supplier, yesterday's upload, etc.
    """
    err = require_restaurant(ctx)
    if err:
        return err

    intent = (question or q or "").strip()
    intent_l = intent.lower()

    # Insurance expiry — prefer ComplianceDocument INSURANCE
    if any(k in intent_l for k in ("insur", "assurance")) and any(
        k in intent_l for k in ("expir", "expire", "renew", "when")
    ):
        found = find_documents(ctx, q="insurance", kind="compliance", limit=10)
        docs = (found.data or {}).get("documents") or []
        # Prefer ones with expiry
        with_exp = [d for d in docs if d.get("expiry_date") or (d.get("structured") or {}).get("expiry_date")]
        pick = (with_exp or docs)[:3]
        if not pick:
            # Fallback tenant files tagged insurance
            found2 = find_documents(ctx, q="insurance", kind="tenant", limit=10)
            pick = (found2.data or {}).get("documents") or []
        if not pick:
            return fail(
                code="insurance_not_found",
                message="I don't have an insurance document with an expiry date on file yet.",
            )
        answers = []
        for d in pick:
            exp = d.get("expiry_date") or (d.get("structured") or {}).get("expiry_date")
            answers.append(f"{d.get('title') or 'Insurance'}: expires {exp or 'date not set'}")
        return ok(
            message="; ".join(answers) + ".",
            verified=True,
            data={"documents": pick, "answers": answers, "structured": pick[0].get("structured")},
            miya_directive="Relay the expiry date from structured fields only.",
        )

    # Show insurance document
    if any(k in intent_l for k in ("insur", "assurance")) and any(
        k in intent_l for k in ("show", "open", "send", "voir", "montre")
    ):
        return show_document(ctx, q="insurance", document_id=document_id)

    # Invoice amount / supplier / yesterday
    if any(k in intent_l for k in ("invoice", "facture", "bill")) or document_id:
        since_arg = since
        if "yesterday" in intent_l or "hier" in intent_l:
            since_arg = since_arg or "yesterday"
        found = find_documents(
            ctx,
            q=q or ("invoice" if not document_id else ""),
            kind="invoice" if not document_id else "",
            since=since_arg,
            days=days,
            limit=10,
        )
        if document_id:
            return get_document(ctx, document_id=document_id)
        docs = (found.data or {}).get("documents") or []
        if not docs:
            return fail(code="invoice_not_found", message="I couldn't find that invoice.")
        if len(docs) > 1 and not any(k in intent_l for k in ("yesterday", "hier", "list")):
            # Still answer if all share same question type
            pass
        pick = docs[0]
        s = pick.get("structured") or pick.get("fields") or {}
        if any(k in intent_l for k in ("amount", "total", "how much", "montant")):
            if s.get("amount") or pick.get("amount"):
                cur = s.get("currency") or pick.get("currency") or ""
                amt = s.get("amount") or pick.get("amount")
                return ok(
                    message=f"The amount on {pick.get('title') or 'the invoice'} is {amt}{(' ' + cur) if cur else ''}.",
                    verified=True,
                    data={"document": pick, "structured": s, "documents": docs},
                )
        if any(k in intent_l for k in ("supplier", "vendor", "fournisseur")):
            vendor = s.get("vendor") or pick.get("vendor")
            if vendor:
                return ok(
                    message=f"The supplier on {pick.get('title') or 'the invoice'} is {vendor}.",
                    verified=True,
                    data={"document": pick, "structured": s, "documents": docs},
                )
        if "yesterday" in intent_l or "hier" in intent_l or "happened" in intent_l or "what" in intent_l:
            lines = []
            for d in docs[:5]:
                st = d.get("structured") or {}
                lines.append(
                    f"{d.get('title') or 'Document'} — "
                    f"vendor {st.get('vendor') or d.get('vendor') or 'n/a'}, "
                    f"amount {st.get('amount') or d.get('amount') or 'n/a'}, "
                    f"status {d.get('status') or 'stored'}"
                )
            return ok(
                message="Here's what I have: " + "; ".join(lines) + ".",
                verified=True,
                data={"documents": docs},
            )
        fact = answer_from_structured(s, intent=intent) or _detail_message(pick)
        return ok(
            message=fact,
            verified=True,
            data={"document": pick, "structured": s, "documents": docs},
        )

    # Generic: find + answer from structured
    found = find_documents(ctx, q=q or intent, since=since, days=days, limit=10)
    if not found.success:
        return found
    docs = (found.data or {}).get("documents") or []
    pick = docs[0]
    s = pick.get("structured") or pick.get("fields") or {}
    fact = answer_from_structured(s, intent=intent) or _detail_message(pick)
    return ok(
        message=fact,
        verified=True,
        data={"document": pick, "structured": s, "documents": docs},
        miya_directive="Use structured fields only; do not invent amounts or dates.",
    )


# Back-compat aliases used by tool names
def list_tenant_documents(ctx: OpsContext, *, q: str = "", limit: int = 20, since: str = "") -> OpsResult:
    return find_documents(ctx, q=q, kind="tenant", since=since, limit=limit)


def get_tenant_document(ctx: OpsContext, *, document_id: str = "", q: str = "") -> OpsResult:
    return get_document(ctx, document_id=document_id, q=q, kind="tenant")
