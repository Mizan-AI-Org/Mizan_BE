"""
PayGuard — amount-tiered hierarchical payment approval for invoices.

Rules live on Restaurant.general_settings['payment_approval'].
Runtime state: InvoicePaymentApproval + InvoicePaymentApprovalStep.
"""
from __future__ import annotations

import logging
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

SETTINGS_KEY = "payment_approval"

DEFAULT_POLICY: dict[str, Any] = {
    "enabled": False,
    "currency": "MAD",
    "currencies": ["MAD"],
    "stuck_hours": 4,
    "max_reminders": 3,
    "tiers": [
        {
            "id": "everyday",
            "name": "Everyday spends",
            "currency": "MAD",
            "max_amount": "5000",
            "accent": "teal",
            "steps": [{"role": "MANAGER", "label": "Manager"}],
        },
        {
            "id": "significant",
            "name": "Significant bills",
            "currency": "MAD",
            "max_amount": "50000",
            "accent": "amber",
            "steps": [
                {"role": "MANAGER", "label": "Ops manager"},
                {"role": "OWNER", "label": "Owner"},
            ],
        },
        {
            "id": "major",
            "name": "Major commitments",
            "currency": "MAD",
            "max_amount": None,
            "accent": "rose",
            "steps": [
                {"role": "MANAGER", "label": "Ops manager"},
                {"role": "OWNER", "label": "Owner"},
                {"role": "ADMIN", "label": "Co-signer"},
            ],
        },
    ],
}


def _dec(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def get_policy(restaurant) -> dict[str, Any]:
    gs = getattr(restaurant, "general_settings", None) or {}
    if not isinstance(gs, dict):
        gs = {}
    raw = gs.get(SETTINGS_KEY)
    if not isinstance(raw, dict):
        return dict(DEFAULT_POLICY)
    policy = {**DEFAULT_POLICY, **raw}
    tiers = raw.get("tiers")
    if isinstance(tiers, list) and tiers:
        policy["tiers"] = tiers
    return policy


def save_policy(restaurant, policy: dict[str, Any]) -> dict[str, Any]:
    gs = dict(getattr(restaurant, "general_settings", None) or {})
    cleaned = sanitize_policy(policy)
    gs[SETTINGS_KEY] = cleaned
    restaurant.general_settings = gs
    restaurant.save(update_fields=["general_settings"])
    return cleaned


def sanitize_policy(policy: dict[str, Any]) -> dict[str, Any]:
    default_currency = str(policy.get("currency") or "MAD")[:8].upper() or "MAD"
    currencies_in = policy.get("currencies")
    currencies: list[str] = []
    if isinstance(currencies_in, list):
        for c in currencies_in:
            code = str(c or "").strip().upper()[:8]
            if code and code not in currencies:
                currencies.append(code)
    if default_currency not in currencies:
        currencies.insert(0, default_currency)
    if not currencies:
        currencies = [default_currency]

    out = {
        "enabled": bool(policy.get("enabled")),
        "currency": default_currency,
        "currencies": currencies[:12],
        "stuck_hours": max(1, min(72, int(policy.get("stuck_hours") or 4))),
        "max_reminders": max(1, min(10, int(policy.get("max_reminders") or 3))),
        "tiers": [],
    }
    tiers_in = policy.get("tiers") if isinstance(policy.get("tiers"), list) else []
    for i, tier in enumerate(tiers_in[:24]):
        if not isinstance(tier, dict):
            continue
        steps_out = []
        for s in (tier.get("steps") or [])[:5]:
            if not isinstance(s, dict):
                continue
            role = str(s.get("role") or "").upper()[:32]
            user_id = str(s.get("user_id") or s.get("userId") or "").strip()
            label = str(s.get("label") or role or "Approver")[:120]
            if not role and not user_id:
                continue
            steps_out.append({"role": role, "user_id": user_id, "label": label})
        if not steps_out:
            continue
        max_amt = tier.get("max_amount")
        if max_amt in ("", "null", "Infinity", "inf"):
            max_amt = None
        tier_currency = str(tier.get("currency") or default_currency)[:8].upper() or default_currency
        if tier_currency not in out["currencies"]:
            out["currencies"].append(tier_currency)
        out["tiers"].append(
            {
                "id": str(tier.get("id") or f"tier_{i}_{uuid.uuid4().hex[:6]}")[:64],
                "name": str(tier.get("name") or f"Tier {i + 1}")[:120],
                "currency": tier_currency,
                "max_amount": str(max_amt) if max_amt is not None else None,
                "accent": str(tier.get("accent") or "teal")[:20],
                "steps": steps_out,
            }
        )
    # Sort by currency, then finite max_amount ascending; open-ended last within currency
    def sort_key(t):
        m = _dec(t.get("max_amount"))
        return (str(t.get("currency") or default_currency), m is None, m or Decimal(0))

    out["tiers"].sort(key=sort_key)
    return out


def resolve_tier(
    policy: dict[str, Any],
    amount,
    currency: str | None = None,
) -> dict[str, Any] | None:
    """Pick the amount band for an invoice in a specific currency."""
    amt = _dec(amount)
    if amt is None:
        return None
    default = str(policy.get("currency") or "MAD").upper()
    cur = str(currency or default).strip().upper() or default
    tiers = [
        t
        for t in (policy.get("tiers") or [])
        if str(t.get("currency") or default).upper() == cur
    ]
    if not tiers:
        return None
    for tier in tiers:
        max_a = _dec(tier.get("max_amount"))
        if max_a is None or amt <= max_a:
            return tier
    return tiers[-1]


def resolve_approvers_for_step(restaurant, step_cfg: dict, *, exclude_ids=None):
    """Return list of CustomUser who can act on this step."""
    from accounts.models import CustomUser

    exclude_ids = set(exclude_ids or [])
    user_id = str(step_cfg.get("user_id") or "").strip()
    role = str(step_cfg.get("role") or "").upper()

    if user_id:
        u = CustomUser.objects.filter(
            id=user_id, restaurant=restaurant, is_active=True
        ).first()
        return [u] if u and str(u.id) not in exclude_ids else []

    role_map = {
        "MANAGER": ["MANAGER", "ADMIN", "OWNER", "SUPER_ADMIN"],
        "ADMIN": ["ADMIN", "OWNER", "SUPER_ADMIN"],
        "OWNER": ["OWNER", "SUPER_ADMIN"],
        "SUPER_ADMIN": ["SUPER_ADMIN"],
        "SUPERVISOR": ["SUPERVISOR", "MANAGER", "ADMIN", "OWNER", "SUPER_ADMIN"],
    }
    roles = role_map.get(role, [role] if role else ["MANAGER", "ADMIN", "OWNER"])
    qs = CustomUser.objects.filter(
        restaurant=restaurant, role__in=roles, is_active=True
    ).exclude(id__in=exclude_ids)
    return list(qs[:8])


def user_can_act_on_step(user, step) -> bool:
    if not user or not user.is_active:
        return False
    if step.required_user_id:
        return str(step.required_user_id) == str(user.id)
    role = (step.required_role or "").upper()
    user_role = (getattr(user, "role", "") or "").upper()
    if user_role in {"SUPER_ADMIN", "OWNER", "ADMIN"}:
        return True
    if not role:
        return user_role in {"MANAGER", "ADMIN", "OWNER", "SUPER_ADMIN"}
    if role == "MANAGER":
        return user_role in {"MANAGER", "ADMIN", "OWNER", "SUPER_ADMIN"}
    if role == "OWNER":
        return user_role in {"OWNER", "SUPER_ADMIN"}
    return user_role == role or user_role in {"OWNER", "SUPER_ADMIN"}


def format_money(amount, currency: str) -> str:
    try:
        a = Decimal(str(amount))
        if a == a.to_integral():
            s = f"{int(a):,}"
        else:
            s = f"{a:,.2f}"
    except Exception:
        s = str(amount)
    return f"{s} {(currency or '').upper()}".strip()


def build_approver_nudge(
    *,
    approver_name: str,
    requester_name: str,
    invoice,
    step_label: str,
    step_index: int,
    total_steps: int,
    is_reminder: bool = False,
    lang: str = "en",
) -> str:
    from core.i18n import tr

    first = (approver_name or "there").split()[0]
    req = (requester_name or "A teammate").split()[0]
    money = format_money(invoice.amount, invoice.currency)
    vendor = invoice.vendor_name or "a vendor"
    inv_no = f" #{invoice.invoice_number}" if invoice.invoice_number else ""
    rung = f"{step_index + 1}/{total_steps}"
    if step_label:
        rung = f"{step_label} ({rung})"

    key = "payguard.nudge_reminder" if is_reminder else "payguard.nudge"
    return tr(
        key,
        lang,
        first=first,
        req=req,
        money=money,
        vendor=vendor,
        inv_no=inv_no,
        rung=rung,
    )


def notify_current_step(approval, *, is_reminder: bool = False) -> int:
    """WhatsApp + in-app nudge to current-step approvers. Returns # notified."""
    from notifications.models import Notification
    from notifications.services import notification_service

    invoice = approval.invoice
    steps = list(approval.steps.order_by("step_order"))
    if not steps or approval.current_step_index >= len(steps):
        return 0
    step = steps[approval.current_step_index]
    policy_step = {
        "role": step.required_role,
        "user_id": str(step.required_user_id or ""),
        "label": step.label,
    }
    approvers = resolve_approvers_for_step(approval.restaurant, policy_step)
    if not approvers and step.required_user:
        approvers = [step.required_user]

    requester = approval.requested_by
    req_name = ""
    if requester:
        req_name = f"{requester.first_name or ''} {requester.last_name or ''}".strip() or (
            requester.email or "A teammate"
        )

    from core.i18n import get_effective_language, tr

    notified = 0
    now = timezone.now()
    for approver in approvers:
        name = f"{approver.first_name or ''} {approver.last_name or ''}".strip() or (
            approver.email or "there"
        )
        lang = get_effective_language(
            user=approver, restaurant=approval.restaurant
        )
        body = build_approver_nudge(
            approver_name=name,
            requester_name=req_name,
            invoice=invoice,
            step_label=step.label or step.required_role or "Approval",
            step_index=approval.current_step_index,
            total_steps=len(steps),
            is_reminder=is_reminder,
            lang=lang,
        )
        title = tr("payguard.title", lang)
        try:
            Notification.objects.create(
                recipient=approver,
                title=title,
                message=body,
                notification_type="PAYMENT_APPROVAL",
                data={
                    "invoice_id": str(invoice.id),
                    "approval_id": str(approval.id),
                    "step_id": str(step.id),
                    "is_reminder": is_reminder,
                },
            )
            notification_service.send_custom_notification(
                recipient=approver,
                message=body,
                notification_type="PAYMENT_APPROVAL",
                title=title,
                channels=["app", "push"],
            )
            phone = getattr(approver, "phone", "") or ""
            if phone.strip():
                notification_service.send_whatsapp_text(phone, body)
            notified += 1
        except Exception:
            logger.exception("PayGuard notify failed approver=%s", approver.pk)

    if notified:
        step.status = step.STATUS_NOTIFIED
        step.notified_at = step.notified_at or now
        step.save(update_fields=["status", "notified_at"])
        approval.last_reminded_at = now
        if is_reminder:
            approval.reminder_count = (approval.reminder_count or 0) + 1
        approval.save(update_fields=["last_reminded_at", "reminder_count", "updated_at"])
    return notified


@transaction.atomic
def start_payment_approval(*, invoice, requested_by=None) -> dict[str, Any]:
    """
    Start PayGuard for an OPEN invoice. If policy disabled or no tier steps,
    marks APPROVED immediately (pay freely).
    """
    from finance.models import (
        Invoice,
        InvoicePaymentApproval,
        InvoicePaymentApprovalStep,
    )

    from core.i18n import get_effective_language, tr

    restaurant = invoice.restaurant
    policy = get_policy(restaurant)
    lang = get_effective_language(user=requested_by, restaurant=restaurant)

    if invoice.status == Invoice.STATUS_PAID:
        return {"success": False, "error": "Invoice already paid"}

    if not policy.get("enabled"):
        invoice.approval_status = Invoice.APPROVAL_APPROVED
        invoice.save(update_fields=["approval_status", "updated_at"])
        return {
            "success": True,
            "status": "approved",
            "message_for_user": tr("payguard.off", lang),
            "approval_required": False,
        }

    inv_currency = str(getattr(invoice, "currency", None) or policy.get("currency") or "MAD").upper()
    tier = resolve_tier(policy, invoice.amount, currency=inv_currency)
    if not tier or not tier.get("steps"):
        invoice.approval_status = Invoice.APPROVAL_APPROVED
        invoice.save(update_fields=["approval_status", "updated_at"])
        return {
            "success": True,
            "status": "approved",
            "message_for_user": tr("payguard.no_tier", lang, currency=inv_currency),
            "approval_required": False,
        }

    # Reset prior run if any
    InvoicePaymentApproval.objects.filter(invoice=invoice).delete()

    approval = InvoicePaymentApproval.objects.create(
        invoice=invoice,
        restaurant=restaurant,
        tier_id=str(tier.get("id") or "")[:64],
        tier_name=str(tier.get("name") or "")[:120],
        requested_by=requested_by,
        status=InvoicePaymentApproval.STATUS_PENDING,
        current_step_index=0,
    )
    for i, scfg in enumerate(tier["steps"]):
        user_id = str(scfg.get("user_id") or "").strip()
        required_user = None
        if user_id:
            from accounts.models import CustomUser

            required_user = CustomUser.objects.filter(
                id=user_id, restaurant=restaurant
            ).first()
        InvoicePaymentApprovalStep.objects.create(
            approval=approval,
            step_order=i,
            label=str(scfg.get("label") or "")[:120],
            required_role=str(scfg.get("role") or "")[:32],
            required_user=required_user,
            status=InvoicePaymentApprovalStep.STATUS_WAITING,
        )

    invoice.approval_status = Invoice.APPROVAL_PENDING
    invoice.save(update_fields=["approval_status", "updated_at"])

    notified = notify_current_step(approval, is_reminder=False)
    money = format_money(invoice.amount, invoice.currency)
    return {
        "success": True,
        "status": "pending_approval",
        "approval_required": True,
        "approval_id": str(approval.id),
        "tier_name": approval.tier_name,
        "steps": serialize_approval(approval)["steps"],
        "notified": notified,
        "message_for_user": tr(
            "payguard.started", lang, money=money, tier=approval.tier_name
        ),
    }


@transaction.atomic
def act_on_approval(
    *,
    invoice,
    actor,
    action: str,
    note: str = "",
) -> dict[str, Any]:
    """Approve or reject the current PayGuard step."""
    from core.i18n import get_effective_language, tr
    from finance.models import Invoice, InvoicePaymentApproval, InvoicePaymentApprovalStep

    action = (action or "").strip().lower()
    if action not in {"approve", "reject"}:
        return {"success": False, "error": "action must be approve or reject"}

    lang = get_effective_language(
        user=actor, restaurant=getattr(invoice, "restaurant", None)
    )

    try:
        approval = invoice.payment_approval
    except InvoicePaymentApproval.DoesNotExist:
        return {"success": False, "error": "No active PayGuard run for this invoice"}

    if approval.status != InvoicePaymentApproval.STATUS_PENDING:
        return {
            "success": False,
            "error": f"Approval is already {approval.status.lower()}",
            "message_for_user": tr(
                "payguard.already", lang, status=approval.status.lower()
            ),
        }

    steps = list(approval.steps.order_by("step_order"))
    if approval.current_step_index >= len(steps):
        return {"success": False, "error": "No current step"}

    step = steps[approval.current_step_index]
    if not user_can_act_on_step(actor, step):
        return {
            "success": False,
            "error": "Not authorized for this rung",
            "message_for_user": tr("payguard.not_authorized", lang),
        }

    now = timezone.now()
    if action == "reject":
        step.status = InvoicePaymentApprovalStep.STATUS_REJECTED
        step.acted_by = actor
        step.acted_at = now
        step.note = (note or "")[:2000]
        step.save()
        approval.status = InvoicePaymentApproval.STATUS_REJECTED
        approval.completed_at = now
        approval.save(update_fields=["status", "completed_at", "updated_at"])
        invoice.approval_status = Invoice.APPROVAL_REJECTED
        invoice.save(update_fields=["approval_status", "updated_at"])
        return {
            "success": True,
            "status": "rejected",
            "message_for_user": tr(
                "payguard.rejected",
                lang,
                vendor=invoice.vendor_name,
                money=format_money(invoice.amount, invoice.currency),
            ),
        }

    # approve current step
    step.status = InvoicePaymentApprovalStep.STATUS_APPROVED
    step.acted_by = actor
    step.acted_at = now
    step.note = (note or "")[:2000]
    step.save()

    next_idx = approval.current_step_index + 1
    if next_idx >= len(steps):
        approval.status = InvoicePaymentApproval.STATUS_APPROVED
        approval.completed_at = now
        approval.current_step_index = next_idx
        approval.save(
            update_fields=["status", "completed_at", "current_step_index", "updated_at"]
        )
        invoice.approval_status = Invoice.APPROVAL_APPROVED
        invoice.save(update_fields=["approval_status", "updated_at"])
        return {
            "success": True,
            "status": "approved",
            "message_for_user": tr(
                "payguard.complete",
                lang,
                money=format_money(invoice.amount, invoice.currency),
                vendor=invoice.vendor_name,
            ),
        }

    approval.current_step_index = next_idx
    approval.reminder_count = 0
    approval.last_reminded_at = None
    approval.save(
        update_fields=[
            "current_step_index",
            "reminder_count",
            "last_reminded_at",
            "updated_at",
        ]
    )
    notified = notify_current_step(approval, is_reminder=False)
    nxt = steps[next_idx]
    return {
        "success": True,
        "status": "pending_approval",
        "current_step": next_idx + 1,
        "total_steps": len(steps),
        "notified": notified,
        "message_for_user": tr(
            "payguard.next_rung",
            lang,
            cleared=next_idx,
            label=nxt.label or nxt.required_role or "—",
            current=next_idx + 1,
            total=len(steps),
        ),
    }


def payment_allowed(invoice) -> tuple[bool, str]:
    """Whether mark_paid may proceed under PayGuard."""
    from core.i18n import get_effective_language, tr
    from finance.models import Invoice

    restaurant = invoice.restaurant
    lang = get_effective_language(restaurant=restaurant)
    policy = get_policy(restaurant)
    if not policy.get("enabled"):
        return True, ""
    if invoice.approval_status in (Invoice.APPROVAL_APPROVED, Invoice.APPROVAL_NONE):
        # NONE with policy on: require starting approval first for OPEN bills
        if invoice.approval_status == Invoice.APPROVAL_NONE and invoice.status == Invoice.STATUS_OPEN:
            # Auto-start not done yet — block until submitted
            return False, tr("payguard.need_first", lang)
        return True, ""
    if invoice.approval_status == Invoice.APPROVAL_PENDING:
        return False, tr("payguard.still_waiting", lang)
    if invoice.approval_status == Invoice.APPROVAL_REJECTED:
        return False, tr("payguard.was_rejected", lang)
    return True, ""


def serialize_approval(approval) -> dict[str, Any]:
    steps = []
    for s in approval.steps.order_by("step_order"):
        steps.append(
            {
                "id": str(s.id),
                "step_order": s.step_order,
                "label": s.label,
                "required_role": s.required_role,
                "required_user_id": str(s.required_user_id) if s.required_user_id else None,
                "required_user_name": (
                    f"{s.required_user.first_name} {s.required_user.last_name}".strip()
                    if s.required_user_id and s.required_user
                    else None
                ),
                "status": s.status,
                "acted_by": (
                    f"{s.acted_by.first_name} {s.acted_by.last_name}".strip()
                    if s.acted_by_id and s.acted_by
                    else None
                ),
                "acted_at": s.acted_at.isoformat() if s.acted_at else None,
                "note": s.note or "",
                "is_current": (
                    approval.status == approval.STATUS_PENDING
                    and s.step_order == approval.current_step_index
                ),
            }
        )
    return {
        "id": str(approval.id),
        "invoice_id": str(approval.invoice_id),
        "tier_id": approval.tier_id,
        "tier_name": approval.tier_name,
        "status": approval.status,
        "current_step_index": approval.current_step_index,
        "reminder_count": approval.reminder_count,
        "last_reminded_at": (
            approval.last_reminded_at.isoformat() if approval.last_reminded_at else None
        ),
        "started_at": approval.started_at.isoformat() if approval.started_at else None,
        "steps": steps,
    }


def serialize_policy_for_ui(policy: dict[str, Any]) -> dict[str, Any]:
    return sanitize_policy(policy) if policy else dict(DEFAULT_POLICY)
