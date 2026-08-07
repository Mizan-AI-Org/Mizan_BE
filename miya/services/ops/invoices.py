"""Canonical invoice lifecycle — store → approve → pay → proof → audit."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db.models import Q
from django.utils import timezone

from miya.services.ops.context import OpsContext, assert_location_access, require_permission, require_restaurant
from miya.services.ops.result import OpsResult, clarify, fail, ok


def _dec(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, TypeError, ValueError):
        return None


def _serialize_invoice(inv) -> dict[str, Any]:
    loc = getattr(inv, "location", None)
    loc_name = ""
    if loc:
        loc_name = getattr(loc, "name", None) or getattr(loc, "label", None) or str(loc.id)
    proof = False
    try:
        proof = bool(inv.proof_of_payment and inv.proof_of_payment.name)
    except Exception:
        proof = False
    approval_meta: dict[str, Any] = {}
    try:
        approval = inv.payment_approval
        approval_meta = {
            "approval_run_status": approval.status,
            "tier_name": approval.tier_name,
            "current_step_index": approval.current_step_index,
            "steps_total": approval.steps.count(),
        }
    except Exception:
        pass
    return {
        "id": str(inv.id),
        "kind": "invoice",
        "vendor": inv.vendor_name,
        "vendor_name": inv.vendor_name,
        "supplier": inv.vendor_name,
        "amount": str(inv.amount) if inv.amount is not None else None,
        "currency": inv.currency,
        "invoice_number": inv.invoice_number or None,
        "status": inv.status,
        "lifecycle_status": getattr(inv, "lifecycle_status", inv.status),
        "approval_status": inv.approval_status,
        "due_date": inv.due_date.isoformat() if inv.due_date else None,
        "issue_date": inv.issue_date.isoformat() if inv.issue_date else None,
        "paid_at": inv.paid_at.isoformat() if getattr(inv, "paid_at", None) else None,
        "created_at": inv.created_at.isoformat() if inv.created_at else None,
        "updated_at": inv.updated_at.isoformat() if getattr(inv, "updated_at", None) else None,
        "location_id": str(inv.location_id) if inv.location_id else None,
        "location_name": loc_name or None,
        "has_payment_proof": proof,
        "bank_payment_status": getattr(inv, "bank_payment_status", None) or None,
        "ocr_fields": getattr(inv, "ocr_fields", None) or {},
        "structured": {
            "vendor": inv.vendor_name,
            "amount": str(inv.amount) if inv.amount is not None else None,
            "currency": inv.currency,
            "invoice_number": inv.invoice_number or None,
            "due_date": inv.due_date.isoformat() if inv.due_date else None,
            "establishment": loc_name or None,
        },
        **approval_meta,
    }


def _since_bounds(since: str = "", days: int | None = None):
    from datetime import datetime as dt

    s = (since or "").strip().lower()
    if s in ("yesterday", "hier"):
        day = timezone.localdate() - timedelta(days=1)
        start = timezone.make_aware(dt.combine(day, dt.min.time()))
        return start, start + timedelta(days=1)
    if s in ("today", "aujourd'hui", "aujourdhui"):
        day = timezone.localdate()
        start = timezone.make_aware(dt.combine(day, dt.min.time()))
        return start, start + timedelta(days=1)
    if days is not None:
        return timezone.now() - timedelta(days=max(0, int(days))), None
    return None, None


def _resolve_location(restaurant, location_id: str = "", location_name: str = ""):
    from accounts.models import BusinessLocation

    lid = (location_id or "").strip()
    if lid:
        loc = BusinessLocation.objects.filter(restaurant=restaurant, id=lid).first()
        if loc:
            return loc
    name = (location_name or "").strip()
    if not name:
        return None
    loc = BusinessLocation.objects.filter(
        restaurant=restaurant, name__iexact=name, is_active=True
    ).first()
    if loc:
        return loc
    return BusinessLocation.objects.filter(
        restaurant=restaurant, name__icontains=name, is_active=True
    ).first()


def resolve_invoice(
    ctx: OpsContext,
    *,
    invoice_id: str = "",
    vendor: str = "",
    invoice_number: str = "",
    q: str = "",
    allow_multiple: bool = False,
) -> tuple[Any | None, OpsResult | None]:
    """Return (invoice, None) or (None, fail/clarify). Never silently pick among many."""
    from finance.models import Invoice
    from miya.services.ops.context import guard_entity_location
    from miya.services.ops.scoping import apply_location_scope, filter_visible_location_ids

    iid = (invoice_id or "").strip()
    if iid and iid.lower() not in ("it", "that", "this", "them", "les"):
        inv = Invoice.objects.filter(restaurant=ctx.restaurant, id=iid).first()
        if inv:
            loc_err = guard_entity_location(ctx, inv)
            if loc_err:
                return None, loc_err
            return inv, None
        if len(iid) >= 8:
            inv = (
                Invoice.objects.filter(restaurant=ctx.restaurant, id__istartswith=iid[:8])
                .exclude(status=Invoice.STATUS_VOIDED)
                .order_by("-created_at")
                .first()
            )
            if inv:
                loc_err = guard_entity_location(ctx, inv)
                if loc_err:
                    return None, loc_err
                return inv, None

    qs = Invoice.objects.filter(restaurant=ctx.restaurant).exclude(status=Invoice.STATUS_VOIDED)
    if ctx.location_id:
        qs = apply_location_scope(qs, location_id=ctx.location_id, field="location_id")
    elif len(ctx.available_locations) > 1:
        qs = filter_visible_location_ids(
            qs,
            location_ids=[r["id"] for r in ctx.available_locations],
            field="location_id",
        )
    vend = (vendor or "").strip()
    num = (invoice_number or "").strip()
    needle = (q or "").strip()
    if vend:
        qs = qs.filter(vendor_name__icontains=vend)
    if num:
        qs = qs.filter(invoice_number__iexact=num)
    if needle and not vend and not num:
        qs = qs.filter(
            Q(vendor_name__icontains=needle)
            | Q(invoice_number__icontains=needle)
            | Q(notes__icontains=needle)
        )
    if not vend and not num and not needle and not iid:
        return None, fail(code="invoice_required", message="Which invoice? Give vendor, number, or id.")

    rows = list(qs.order_by("-created_at", "-due_date")[:8])
    if not rows:
        return None, fail(code="invoice_not_found", message="I couldn't find that invoice.")
    if len(rows) == 1 or allow_multiple:
        return rows[0], None
    candidates = [_serialize_invoice(r) for r in rows]
    return None, clarify(
        message="Several invoices match — which one?",
        data={"invoices": candidates, "count": len(candidates)},
    )


def find_invoices(
    ctx: OpsContext,
    *,
    q: str = "",
    vendor: str = "",
    status: str = "",
    since: str = "",
    days: int | None = None,
    limit: int = 20,
) -> OpsResult:
    err = require_restaurant(ctx) or require_permission(ctx, "run_reports")
    if err:
        return err

    from miya.services.ops.context import require_establishment_context
    from miya.services.ops.scoping import apply_location_scope, filter_visible_location_ids

    est_err = require_establishment_context(ctx, for_action="invoices")
    if est_err:
        return est_err

    from finance.models import Invoice

    qs = Invoice.objects.filter(restaurant=ctx.restaurant).select_related("location")
    if ctx.location_id:
        qs = apply_location_scope(qs, location_id=ctx.location_id, field="location_id")
    elif len(ctx.available_locations) > 1:
        qs = filter_visible_location_ids(
            qs,
            location_ids=[r["id"] for r in ctx.available_locations],
            field="location_id",
        )
    start, end = _since_bounds(since, days)
    if start is not None:
        qs = qs.filter(created_at__gte=start)
    if end is not None:
        qs = qs.filter(created_at__lt=end)

    needle = (q or vendor or "").strip()
    if needle:
        qs = qs.filter(
            Q(vendor_name__icontains=needle)
            | Q(invoice_number__icontains=needle)
            | Q(notes__icontains=needle)
        )
    st = (status or "").strip().upper()
    if st and st not in ("ALL", "*"):
        if st in ("UNPAID", "OPEN"):
            qs = qs.filter(status__in=Invoice.UNPAID_ACTIVE_STATUSES)
        elif st == "PENDING_APPROVAL":
            qs = qs.filter(
                Q(status=Invoice.STATUS_PENDING_APPROVAL)
                | Q(approval_status=Invoice.APPROVAL_PENDING)
            )
        else:
            qs = qs.filter(status__iexact=st)

    rows = [_serialize_invoice(inv) for inv in qs.order_by("-created_at")[: max(1, min(int(limit or 20), 40))]]
    if not rows:
        return fail(
            code="invoices_not_found",
            message="No invoices match that filter.",
            data={"invoices": [], "documents": [], "count": 0},
        )
    return ok(
        message=f"Found {len(rows)} invoice(s).",
        verified=True,
        data={"invoices": rows, "documents": rows, "count": len(rows)},
        miya_directive="Relay lifecycle_status and amount/vendor from this payload. For history use get_invoice_timeline.",
    )


def get_invoice(
    ctx: OpsContext,
    *,
    invoice_id: str = "",
    vendor: str = "",
    invoice_number: str = "",
    q: str = "",
) -> OpsResult:
    err = require_restaurant(ctx) or require_permission(ctx, "run_reports")
    if err:
        return err
    inv, err2 = resolve_invoice(
        ctx, invoice_id=invoice_id, vendor=vendor or q, invoice_number=invoice_number, q=q
    )
    if err2:
        return err2
    row = _serialize_invoice(inv)
    return ok(
        message=(
            f"Invoice from {row['vendor']}: {row['amount']} {row['currency']} — "
            f"{row['lifecycle_status']}"
            + (f" (#{row['invoice_number']})" if row.get("invoice_number") else "")
            + "."
        ),
        verified=True,
        data={"invoice": row, "invoices": [row]},
    )


def get_invoice_timeline(
    ctx: OpsContext,
    *,
    invoice_id: str = "",
    vendor: str = "",
    invoice_number: str = "",
    q: str = "",
) -> OpsResult:
    """Live audit history — never stale list cache."""
    err = require_restaurant(ctx) or require_permission(ctx, "run_reports")
    if err:
        return err

    from finance.timeline import build_invoice_timeline, summarize_timeline_for_miya

    inv, err2 = resolve_invoice(
        ctx,
        invoice_id=invoice_id,
        vendor=vendor or q,
        invoice_number=invoice_number,
        q=q if not vendor else "",
    )
    if err2:
        return err2

    # Fresh read
    from finance.models import Invoice

    fresh = Invoice.objects.filter(id=inv.id, restaurant=ctx.restaurant).select_related("location").first()
    if not fresh:
        return fail(code="invoice_not_found", message="I couldn't find that invoice.")

    events = build_invoice_timeline(fresh)
    summary = summarize_timeline_for_miya(fresh, events)
    row = _serialize_invoice(fresh)
    return ok(
        message=summary,
        verified=True,
        data={
            "invoice": row,
            "events": events,
            "summary": summary,
            "lifecycle_status": row["lifecycle_status"],
            "has_payment_proof": row["has_payment_proof"],
        },
        miya_directive=(
            "Relay the summary and current lifecycle_status from THIS payload only. "
            "Do not use earlier list snapshots — this is the live history."
        ),
    )


def check_amount_and_tier(ctx: OpsContext, *, invoice_id: str = "", vendor: str = "") -> OpsResult:
    """CHECK AMOUNT → DETERMINE APPROVAL tier without starting yet."""
    err = require_restaurant(ctx) or require_permission(ctx, "run_reports")
    if err:
        return err
    from finance.payment_approval import format_money, get_policy, resolve_tier

    inv, err2 = resolve_invoice(ctx, invoice_id=invoice_id, vendor=vendor)
    if err2:
        return err2
    policy = get_policy(ctx.restaurant)
    enabled = bool(policy.get("enabled"))
    tier = resolve_tier(policy, inv.amount, currency=inv.currency) if enabled else None
    money = format_money(inv.amount, inv.currency)
    if not enabled:
        msg = f"{money} from {inv.vendor_name} — PayGuard is off; payment does not need approval."
        required = False
    elif not tier:
        msg = f"{money} from {inv.vendor_name} — no matching approval tier; payment can proceed."
        required = False
    else:
        steps = tier.get("steps") or []
        labels = ", ".join(
            (s.get("label") or s.get("role") or "approver") for s in steps if isinstance(s, dict)
        )
        msg = (
            f"{money} from {inv.vendor_name} falls in '{tier.get('name') or tier.get('id')}' "
            f"({len(steps)} approval step(s): {labels})."
        )
        required = bool(steps)
    return ok(
        message=msg,
        verified=True,
        data={
            "invoice": _serialize_invoice(inv),
            "policy_enabled": enabled,
            "approval_required": required,
            "tier": tier,
        },
    )


def record_invoice(
    ctx: OpsContext,
    *,
    vendor: str,
    amount,
    due_date=None,
    currency: str = "",
    invoice_number: str = "",
    issue_date=None,
    notes: str = "",
    location_id: str = "",
    location_name: str = "",
    photo_url: str = "",
    category: str = "",
    start_approval: bool = True,
    document_id: str = "",
) -> OpsResult:
    """STORE + IDENTIFY SUPPLIER/ESTABLISHMENT + optional PayGuard start."""
    err = require_restaurant(ctx) or require_permission(ctx, "run_reports")
    if err:
        return err

    from datetime import date as date_cls
    from django.utils.dateparse import parse_date
    from finance.audit import InvoiceAuditEvent, log_invoice_event
    from finance.models import Invoice
    from finance.payment_approval import get_policy, start_payment_approval

    vend = (vendor or "").strip()
    if not vend:
        return fail(code="vendor_required", message="I need the supplier/vendor name.")
    amt = _dec(amount)
    if amt is None or amt <= 0:
        return fail(code="amount_required", message="I need a positive invoice amount.")

    due = due_date
    if isinstance(due, str):
        due = parse_date(due.strip()[:32])
    if due is None:
        due = timezone.localdate() + timedelta(days=30)

    issue = issue_date
    if isinstance(issue, str):
        issue = parse_date(issue.strip()[:32])

    cur = (currency or getattr(ctx.restaurant, "currency", None) or "MAD").upper()[:8]
    num = (invoice_number or "").strip()[:120]

    if num:
        existing = Invoice.objects.filter(
            restaurant=ctx.restaurant,
            vendor_name__iexact=vend,
            invoice_number__iexact=num,
        ).first()
        if existing:
            return ok(
                message=f"That invoice from {vend} (#{num}) is already on file — {_serialize_invoice(existing)['lifecycle_status']}.",
                verified=True,
                data={"invoice": _serialize_invoice(existing), "created": False},
            )

    location = _resolve_location(ctx.restaurant, location_id, location_name)
    # Also try OCR-ish location from notes
    if location is None and notes:
        location = _resolve_location(ctx.restaurant, "", notes[:80])
    if location is None and ctx.location_id:
        from accounts.models import BusinessLocation

        location = BusinessLocation.objects.filter(
            id=ctx.location_id, restaurant=ctx.restaurant
        ).first()
    denied = assert_location_access(ctx, str(location.id) if location else None)
    if denied:
        return denied
    if not location and len(ctx.available_locations) > 1:
        from miya.services.ops.context import require_establishment_context

        est_err = require_establishment_context(ctx, for_action="recording an invoice")
        if est_err:
            return est_err

    invoice = Invoice.objects.create(
        restaurant=ctx.restaurant,
        location=location,
        vendor_name=vend[:200],
        invoice_number=num,
        amount=amt,
        currency=cur,
        issue_date=issue if isinstance(issue, date_cls) else None,
        due_date=due,
        status=Invoice.STATUS_OPEN,
        category=(category or "payables")[:50],
        notes=(notes or "")[:2000],
        photo_url=(photo_url or "")[:1024],
        created_by=ctx.user if getattr(ctx.user, "pk", None) else None,
    )
    try:
        log_invoice_event(
            invoice,
            InvoiceAuditEvent.EVENT_CREATED,
            actor=ctx.user if getattr(ctx.user, "pk", None) else None,
            channel=InvoiceAuditEvent.CHANNEL_MIYA,
            summary=f"Invoice recorded — {vend}, {cur} {amt}, due {due}.",
            metadata={
                "invoice_number": num,
                "location_id": str(location.id) if location else None,
                "source": "ops.record_invoice",
            },
        )
    except Exception:
        pass

    if photo_url:
        try:
            from finance.attachment_utils import attach_invoice_from_url

            attach_invoice_from_url(invoice, photo_url)
        except Exception:
            pass

    if document_id:
        try:
            from miya.models import TenantDocument
            from miya.services.tenant_documents import serialize_tenant_document

            doc = TenantDocument.objects.filter(
                id=document_id, restaurant_id=ctx.restaurant_id
            ).first()
            if doc:
                doc.invoice = invoice
                # Prefer stored file URL as photo evidence when missing
                if not invoice.photo_url:
                    try:
                        row = serialize_tenant_document(doc)
                        href = (row.get("file_url") or "")[:1024]
                        if href:
                            invoice.photo_url = href
                            invoice.save(update_fields=["photo_url"])
                    except Exception:
                        pass
                doc.save(update_fields=["invoice", "updated_at"])
        except Exception:
            pass

    payguard = None
    msg = f"Logged {cur} {amt} invoice from {vend}" + (f" (#{num})" if num else "") + f", due {due}."
    if location:
        msg += f" Establishment: {getattr(location, 'name', location)}."
    if start_approval and get_policy(ctx.restaurant).get("enabled"):
        try:
            payguard = start_payment_approval(
                invoice=invoice,
                requested_by=ctx.user if getattr(ctx.user, "pk", None) else None,
            )
            if payguard.get("approval_required"):
                msg += " " + (payguard.get("message_for_user") or "PayGuard approval requested.")
            elif payguard.get("status") == "approved":
                msg += " Auto-approved under PayGuard policy."
        except Exception:
            pass

    fresh = Invoice.objects.filter(id=invoice.id).select_related("location").first()
    if not fresh:
        return fail(code="verify_failed", message="I tried to save the invoice but couldn't verify it.")
    row = _serialize_invoice(fresh)
    return ok(
        message=msg,
        verified=True,
        data={"invoice": row, "created": True, "payguard": payguard},
    )


def request_approval(ctx: OpsContext, *, invoice_id: str = "", vendor: str = "") -> OpsResult:
    err = require_restaurant(ctx) or require_permission(ctx, "run_reports")
    if err:
        return err
    from finance.payment_approval import start_payment_approval
    from finance.models import Invoice

    inv, err2 = resolve_invoice(ctx, invoice_id=invoice_id, vendor=vendor)
    if err2:
        return err2
    result = start_payment_approval(
        invoice=inv, requested_by=ctx.user if getattr(ctx.user, "pk", None) else None
    )
    if not result.get("success"):
        return fail(
            code=str(result.get("error") or "approval_failed"),
            message=result.get("message_for_user") or result.get("error") or "Could not start approval.",
            data={"payguard": result},
        )
    fresh = Invoice.objects.filter(id=inv.id).first()
    return ok(
        message=result.get("message_for_user") or "Approval requested.",
        verified=True,
        data={"invoice": _serialize_invoice(fresh or inv), "payguard": result},
    )


def payment_approval_action(
    ctx: OpsContext,
    *,
    action: str = "list",
    invoice_id: str = "",
    vendor: str = "",
    note: str = "",
) -> OpsResult:
    err = require_restaurant(ctx) or require_permission(ctx, "run_reports")
    if err:
        return err

    from finance.models import Invoice, InvoicePaymentApproval
    from finance.payment_approval import (
        act_on_approval,
        get_policy,
        serialize_approval,
        serialize_policy_for_ui,
        start_payment_approval,
    )

    act = (action or "list").strip().lower()
    if act in ("list", "pending", ""):
        qs = (
            InvoicePaymentApproval.objects.filter(
                restaurant=ctx.restaurant, status=InvoicePaymentApproval.STATUS_PENDING
            )
            .select_related("invoice")
            .prefetch_related("steps")
            .order_by("started_at")[:20]
        )
        rows = []
        for a in qs:
            row = serialize_approval(a)
            row["invoice"] = _serialize_invoice(a.invoice)
            rows.append(row)
        return ok(
            message=(
                f"{len(rows)} payment(s) waiting on PayGuard."
                if rows
                else "No payments waiting for approval."
            ),
            verified=True,
            data={
                "approvals": rows,
                "count": len(rows),
                "policy_enabled": bool(get_policy(ctx.restaurant).get("enabled")),
            },
        )

    if act in ("get_policy", "policy"):
        return ok(
            message="PayGuard policy loaded.",
            verified=True,
            data={"policy": serialize_policy_for_ui(get_policy(ctx.restaurant))},
        )

    inv, err2 = resolve_invoice(ctx, invoice_id=invoice_id, vendor=vendor)
    if err2:
        return err2

    if act == "start":
        result = start_payment_approval(
            invoice=inv, requested_by=ctx.user if getattr(ctx.user, "pk", None) else None
        )
        if not result.get("success"):
            return fail(
                code=str(result.get("error") or "start_failed"),
                message=result.get("message_for_user") or "Could not start approval.",
            )
        fresh = Invoice.objects.filter(id=inv.id).first()
        return ok(
            message=result.get("message_for_user") or "Approval started.",
            verified=True,
            data={"invoice": _serialize_invoice(fresh or inv), "payguard": result},
        )

    if act in ("approve", "reject", "request_info"):
        result = act_on_approval(
            invoice=inv,
            actor=ctx.user,
            action=act,
            note=note or "",
        )
        if not result.get("success"):
            return fail(
                code=str(result.get("error") or "approval_action_failed"),
                message=result.get("message_for_user") or result.get("error") or "Action failed.",
                data={"payguard": result},
            )
        fresh = Invoice.objects.filter(id=inv.id).select_related("location").first()
        row = _serialize_invoice(fresh or inv)
        # Verify status flipped for terminal actions
        if act == "approve" and result.get("status") == "approved":
            if fresh and fresh.approval_status not in (
                Invoice.APPROVAL_APPROVED,
                getattr(Invoice, "APPROVAL_APPROVED", "APPROVED"),
            ):
                # may still be pending next step
                pass
        if act == "reject":
            if fresh and fresh.lifecycle_status not in (Invoice.STATUS_REJECTED, "REJECTED"):
                if fresh.approval_status != Invoice.APPROVAL_REJECTED:
                    return fail(
                        code="verify_failed",
                        message="I couldn't verify the rejection was saved.",
                        data={"invoice": row},
                    )
        return ok(
            message=result.get("message_for_user") or f"Invoice {act}d.",
            verified=True,
            data={"invoice": row, "payguard": result},
        )

    return fail(code="unknown_action", message="Use list, start, approve, reject, or get_policy.")


def mark_invoice_paid(
    ctx: OpsContext,
    *,
    invoice_id: str = "",
    vendor: str = "",
    invoice_number: str = "",
    method: str = "",
    reference: str = "",
    amount=None,
    paid_on=None,
) -> OpsResult:
    err = require_restaurant(ctx) or require_permission(ctx, "run_reports")
    if err:
        return err

    from finance.audit import InvoiceAuditEvent, log_invoice_event
    from finance.models import Invoice
    from finance.payment_approval import payment_allowed, start_payment_approval

    inv, err2 = resolve_invoice(
        ctx, invoice_id=invoice_id, vendor=vendor, invoice_number=invoice_number
    )
    if err2:
        return err2

    if inv.status == Invoice.STATUS_PAID:
        return ok(
            message=f"That {inv.vendor_name} invoice is already marked paid.",
            verified=True,
            data={"invoice": _serialize_invoice(inv), "already_paid": True},
        )

    allowed, block_msg = payment_allowed(inv)
    if not allowed:
        if inv.approval_status == Invoice.APPROVAL_NONE:
            started = start_payment_approval(
                invoice=inv, requested_by=ctx.user if getattr(ctx.user, "pk", None) else None
            )
            return fail(
                code="approval_required",
                message=started.get("message_for_user")
                or "PayGuard needs approval before this can be paid.",
                data={"payguard": started, "invoice": _serialize_invoice(inv)},
            )
        return fail(
            code="approval_required",
            message=block_msg or "This invoice still needs approval before payment.",
            data={"invoice": _serialize_invoice(inv)},
        )

    inv.mark_paid(
        paid_on=paid_on,
        method=(method or "").upper(),
        reference=reference or "",
        amount=_dec(amount),
        user=ctx.user if getattr(ctx.user, "pk", None) else None,
    )
    inv.bank_payment_status = Invoice.BANK_PAYMENT_CLEARED
    inv.save(update_fields=["bank_payment_status", "updated_at"])
    try:
        log_invoice_event(
            inv,
            InvoiceAuditEvent.EVENT_PAYMENT_RECORDED,
            actor=ctx.user if getattr(ctx.user, "pk", None) else None,
            channel=InvoiceAuditEvent.CHANNEL_MIYA,
            summary=f"Payment recorded — {inv.amount} {inv.currency}.",
            metadata={"payment_method": method, "payment_reference": reference},
        )
    except Exception:
        pass

    fresh = Invoice.objects.filter(id=inv.id).first()
    if not fresh or fresh.status != Invoice.STATUS_PAID:
        return fail(code="verify_failed", message="I couldn't verify the invoice was marked paid.")
    return ok(
        message=f"Marked {fresh.vendor_name} invoice ({fresh.amount} {fresh.currency}) as paid.",
        verified=True,
        data={"invoice": _serialize_invoice(fresh)},
    )


def attach_invoice_proof(
    ctx: OpsContext,
    *,
    invoice_id: str = "",
    vendor: str = "",
    proof_url: str = "",
    file_bytes: bytes | None = None,
    filename: str = "proof.jpg",
    mime_type: str = "image/jpeg",
    mark_paid: bool = False,
) -> OpsResult:
    err = require_restaurant(ctx) or require_permission(ctx, "run_reports")
    if err:
        return err

    from django.core.files.base import ContentFile
    from finance.audit import InvoiceAuditEvent, log_invoice_event
    from finance.models import Invoice

    inv, err2 = resolve_invoice(ctx, invoice_id=invoice_id, vendor=vendor)
    if err2:
        return err2

    saved = False
    if file_bytes:
        try:
            inv.proof_of_payment.save(filename or "proof.jpg", ContentFile(file_bytes), save=True)
            saved = True
        except Exception:
            return fail(code="upload_failed", message="I couldn't save that proof of payment file.")
    elif proof_url:
        try:
            from finance.attachment_utils import attach_proof_of_payment_from_url

            saved = bool(attach_proof_of_payment_from_url(inv, proof_url))
        except Exception:
            saved = False
    if not saved:
        return fail(code="proof_required", message="Send a proof file or proof_url.")

    try:
        log_invoice_event(
            inv,
            InvoiceAuditEvent.EVENT_PROOF_UPLOADED,
            actor=ctx.user if getattr(ctx.user, "pk", None) else None,
            channel=InvoiceAuditEvent.CHANNEL_MIYA,
            summary="Payment proof attached.",
        )
    except Exception:
        pass

    inv.refresh_from_db()
    if not (inv.proof_of_payment and inv.proof_of_payment.name):
        return fail(code="verify_failed", message="I couldn't verify the proof was saved.")

    msg = f"Payment proof attached to {inv.vendor_name} invoice."
    if mark_paid and inv.status != Invoice.STATUS_PAID:
        paid = mark_invoice_paid(ctx, invoice_id=str(inv.id))
        if paid.success:
            return ok(
                message=msg + " " + paid.message_for_user,
                verified=True,
                data={**(paid.data or {}), "proof_attached": True},
            )
        return ok(
            message=msg + " " + (paid.message_for_user or "Not marked paid yet (approval may be required)."),
            verified=True,
            data={"invoice": _serialize_invoice(inv), "proof_attached": True, "mark_paid_result": paid.as_tool_response()},
        )

    return ok(
        message=msg,
        verified=True,
        data={"invoice": _serialize_invoice(inv), "proof_attached": True},
    )


def return_invoice(
    ctx: OpsContext,
    *,
    invoice_id: str = "",
    vendor: str = "",
    reason: str = "",
) -> OpsResult:
    err = require_restaurant(ctx) or require_permission(ctx, "run_reports")
    if err:
        return err
    from finance.audit import InvoiceAuditEvent, log_invoice_event
    from finance.models import Invoice

    inv, err2 = resolve_invoice(ctx, invoice_id=invoice_id, vendor=vendor)
    if err2:
        return err2
    notes = (reason or "").strip() or "Returned via Miya"
    inv.status = Invoice.STATUS_RETURNED
    inv.returned_reason = notes[:500]
    inv.save(update_fields=["status", "returned_reason", "updated_at"])
    try:
        log_invoice_event(
            inv,
            InvoiceAuditEvent.EVENT_RETURNED,
            actor=ctx.user if getattr(ctx.user, "pk", None) else None,
            channel=InvoiceAuditEvent.CHANNEL_MIYA,
            summary=f"Invoice returned — {notes[:120]}",
        )
    except Exception:
        pass
    fresh = Invoice.objects.filter(id=inv.id).first()
    if not fresh or fresh.status != Invoice.STATUS_RETURNED:
        return fail(code="verify_failed", message="I couldn't verify the invoice was returned.")
    return ok(
        message=f"Returned {fresh.vendor_name} invoice.",
        verified=True,
        data={"invoice": _serialize_invoice(fresh)},
    )
