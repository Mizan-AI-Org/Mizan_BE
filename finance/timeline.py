"""Build chronological invoice timeline for dashboard and Miya."""
from __future__ import annotations

from typing import Any

from finance.audit import InvoiceAuditEvent


def _format_actor(event: InvoiceAuditEvent) -> str:
    if event.actor_label:
        return event.actor_label
    if event.actor_id:
        u = event.actor
        if u:
            name = f"{u.first_name or ''} {u.last_name or ''}".strip()
            return name or (u.email or str(u.id))
    return "System"


def build_invoice_timeline(invoice) -> list[dict[str, Any]]:
    """Merge audit events + PayGuard steps into one chronological feed."""
    rows: list[dict[str, Any]] = []

    for ev in (
        InvoiceAuditEvent.objects.filter(invoice=invoice)
        .select_related("actor")
        .order_by("created_at", "id")
    ):
        rows.append(
            {
                "id": str(ev.id),
                "kind": "audit",
                "event_type": ev.event_type,
                "at": ev.created_at.isoformat(),
                "actor": _format_actor(ev),
                "channel": ev.channel,
                "summary": ev.summary,
                "metadata": ev.metadata or {},
            }
        )

    try:
        approval = invoice.payment_approval
    except Exception:
        approval = None

    if approval:
        for step in approval.steps.select_related("acted_by").order_by("step_order"):
            if step.notified_at:
                rows.append(
                    {
                        "id": f"step-notify-{step.id}",
                        "kind": "approval_step",
                        "event_type": InvoiceAuditEvent.EVENT_APPROVAL_NOTIFIED,
                        "at": step.notified_at.isoformat(),
                        "actor": step.label or step.required_role or "Approver",
                        "channel": "whatsapp",
                        "summary": f"PayGuard notified {step.label or step.required_role or 'approver'} (step {step.step_order + 1}).",
                        "metadata": {"step_order": step.step_order, "status": step.status},
                    }
                )
            if step.acted_at:
                et = (
                    InvoiceAuditEvent.EVENT_APPROVED
                    if step.status == "APPROVED"
                    else InvoiceAuditEvent.EVENT_REJECTED
                    if step.status == "REJECTED"
                    else InvoiceAuditEvent.EVENT_COMMENT
                )
                actor = ""
                if step.acted_by:
                    actor = f"{step.acted_by.first_name or ''} {step.acted_by.last_name or ''}".strip() or step.acted_by.email
                rows.append(
                    {
                        "id": f"step-act-{step.id}",
                        "kind": "approval_step",
                        "event_type": et,
                        "at": step.acted_at.isoformat(),
                        "actor": actor or step.label or "Approver",
                        "channel": "dashboard",
                        "summary": step.note or f"{step.status} on PayGuard step {step.step_order + 1}.",
                        "metadata": {"step_order": step.step_order, "status": step.status},
                    }
                )

    rows.sort(key=lambda r: r.get("at") or "")
    return rows


def summarize_timeline_for_miya(invoice, events: list[dict[str, Any]] | None = None) -> str:
    """Plain-text chronological summary for Miya replies."""
    events = events if events is not None else build_invoice_timeline(invoice)
    vendor = invoice.vendor_name or "Unknown vendor"
    inv_no = f" #{invoice.invoice_number}" if invoice.invoice_number else ""
    header = (
        f"Invoice{inv_no} — {vendor}: {invoice.amount} {invoice.currency}. "
        f"Status: {getattr(invoice, 'lifecycle_status', invoice.status)}."
    )
    if not events:
        return header + "\nNo audit events recorded yet."
    lines = [header, "", "Timeline:"]
    for ev in events:
        at = (ev.get("at") or "")[:16].replace("T", " ")
        actor = ev.get("actor") or "—"
        summary = ev.get("summary") or ev.get("event_type") or "Event"
        lines.append(f"• {at} — {summary} ({actor})")
    return "\n".join(lines)
