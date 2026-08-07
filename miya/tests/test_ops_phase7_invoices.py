"""Phase 7: invoice lifecycle — threshold, approve/reject, pay, proof, timeline."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from finance.payment_approval import DEFAULT_POLICY, resolve_tier
from miya.services.ops import CANONICAL_TOOL_NAMES, dispatch_canonical_tool
from miya.services.ops.context import OpsContext


def _ctx(*, role="OWNER"):
    rest = MagicMock()
    rest.id = "rest-1"
    rest.general_settings = {
        "payment_approval": {
            **DEFAULT_POLICY,
            "enabled": True,
            "tiers": DEFAULT_POLICY["tiers"],
        }
    }
    rest.currency = "MAD"
    user = MagicMock()
    user.id = "u-owner"
    user.pk = "u-owner"
    user.role = role
    user.is_active = True
    user.first_name = "Owner"
    user.last_name = "One"
    user.email = "o@ex.com"
    return OpsContext(
        user=user,
        restaurant=rest,
        restaurant_id="rest-1",
        user_id="u-owner",
        role=role,
        channel="dashboard",
    )


def _invoice(*, amount="100", vendor="ABC Foods", status="OPEN", approval="NONE"):
    from finance.models import Invoice

    inv = MagicMock()
    inv.id = "inv-abc-1"
    inv.vendor_name = vendor
    inv.amount = Decimal(str(amount))
    inv.currency = "MAD"
    inv.invoice_number = "AF-100"
    inv.status = status
    inv.approval_status = approval
    inv.due_date = None
    inv.issue_date = None
    inv.paid_at = None
    inv.created_at = MagicMock()
    inv.created_at.isoformat.return_value = "2026-08-07T10:00:00+00:00"
    inv.updated_at = inv.created_at
    inv.location = None
    inv.location_id = None
    inv.proof_of_payment = None
    inv.bank_payment_status = ""
    inv.ocr_fields = {"vendor": vendor, "amount": str(amount)}
    inv.lifecycle_status = status
    inv.restaurant = MagicMock()
    # payment_approval related accessor raises by default
    type(inv).payment_approval = property(
        lambda self: (_ for _ in ()).throw(Exception("no approval"))
    )
    inv.STATUS_PAID = Invoice.STATUS_PAID
    inv.STATUS_REJECTED = Invoice.STATUS_REJECTED
    inv.STATUS_RETURNED = Invoice.STATUS_RETURNED
    inv.STATUS_OPEN = Invoice.STATUS_OPEN
    inv.APPROVAL_NONE = Invoice.APPROVAL_NONE
    inv.APPROVAL_PENDING = Invoice.APPROVAL_PENDING
    inv.APPROVAL_APPROVED = Invoice.APPROVAL_APPROVED
    inv.APPROVAL_REJECTED = Invoice.APPROVAL_REJECTED
    inv.BANK_PAYMENT_CLEARED = Invoice.BANK_PAYMENT_CLEARED
    return inv


class TierThresholdTests(SimpleTestCase):
    def test_below_everyday_threshold(self):
        policy = dict(DEFAULT_POLICY)
        policy["enabled"] = True
        tier = resolve_tier(policy, Decimal("500"), currency="MAD")
        self.assertEqual(tier["id"], "everyday")
        self.assertEqual(len(tier["steps"]), 1)

    def test_above_everyday_into_significant(self):
        policy = dict(DEFAULT_POLICY)
        policy["enabled"] = True
        tier = resolve_tier(policy, Decimal("12000"), currency="MAD")
        self.assertEqual(tier["id"], "significant")

    def test_major_open_ended(self):
        policy = dict(DEFAULT_POLICY)
        policy["enabled"] = True
        tier = resolve_tier(policy, Decimal("200000"), currency="MAD")
        self.assertEqual(tier["id"], "major")

    def test_multi_step_tier_preserved(self):
        policy = {
            "enabled": True,
            "currency": "MAD",
            "tiers": [
                {
                    "id": "dual",
                    "name": "Dual approvers",
                    "currency": "MAD",
                    "max_amount": None,
                    "steps": [
                        {"role": "MANAGER", "label": "Manager"},
                        {"role": "OWNER", "label": "Owner"},
                    ],
                }
            ],
        }
        tier = resolve_tier(policy, Decimal("1000"), currency="MAD")
        self.assertEqual(len(tier["steps"]), 2)


class CheckAndLifecycleOpsTests(SimpleTestCase):
    def test_check_amount_below_threshold_message(self):
        from miya.services.ops.invoices import check_amount_and_tier

        ctx = _ctx()
        inv = _invoice(amount="400")
        with patch(
            "miya.services.ops.invoices.require_restaurant", return_value=None
        ), patch(
            "miya.services.ops.invoices.require_permission", return_value=None
        ), patch(
            "miya.services.ops.invoices.resolve_invoice", return_value=(inv, None)
        ), patch(
            "finance.payment_approval.get_policy",
            return_value={**DEFAULT_POLICY, "enabled": True},
        ):
            result = check_amount_and_tier(ctx, invoice_id="inv-abc-1")
        self.assertTrue(result.success)
        self.assertTrue(result.data.get("approval_required"))
        self.assertEqual(result.data.get("tier", {}).get("id"), "everyday")

    def test_approve_and_reject_actions(self):
        from miya.services.ops.invoices import payment_approval_action
        from miya.services.ops.result import ok as unused  # noqa: F401
        from finance.models import Invoice

        ctx = _ctx()
        inv = _invoice(amount="8000", approval="PENDING", status="PENDING_APPROVAL")

        with patch(
            "miya.services.ops.invoices.require_restaurant", return_value=None
        ), patch(
            "miya.services.ops.invoices.require_permission", return_value=None
        ), patch(
            "miya.services.ops.invoices.resolve_invoice", return_value=(inv, None)
        ), patch(
            "finance.payment_approval.act_on_approval",
            return_value={
                "success": True,
                "status": "approved",
                "message_for_user": "Approved.",
            },
        ), patch(
            "finance.models.Invoice.objects"
        ) as mock_objs:
            fresh = _invoice(amount="8000", status="APPROVED", approval="APPROVED")
            fresh.lifecycle_status = "APPROVED"
            mock_objs.filter.return_value.select_related.return_value.first.return_value = fresh
            mock_objs.filter.return_value.first.return_value = fresh
            approved = payment_approval_action(
                ctx, action="approve", invoice_id="inv-abc-1"
            )

        self.assertTrue(approved.success)
        self.assertTrue(approved.verified)

        with patch(
            "miya.services.ops.invoices.require_restaurant", return_value=None
        ), patch(
            "miya.services.ops.invoices.require_permission", return_value=None
        ), patch(
            "miya.services.ops.invoices.resolve_invoice", return_value=(inv, None)
        ), patch(
            "finance.payment_approval.act_on_approval",
            return_value={
                "success": True,
                "status": "rejected",
                "message_for_user": "Rejected.",
            },
        ), patch(
            "finance.models.Invoice.objects"
        ) as mock_objs:
            rejected = _invoice(amount="8000", status="REJECTED", approval="REJECTED")
            rejected.lifecycle_status = Invoice.STATUS_REJECTED
            rejected.approval_status = Invoice.APPROVAL_REJECTED
            mock_objs.filter.return_value.select_related.return_value.first.return_value = rejected
            mock_objs.filter.return_value.first.return_value = rejected
            result = payment_approval_action(ctx, action="reject", invoice_id="inv-abc-1")
        self.assertTrue(result.success)

    def test_mark_paid_blocked_then_allowed(self):
        from miya.services.ops.invoices import mark_invoice_paid
        from finance.models import Invoice

        ctx = _ctx()
        inv = _invoice(amount="9000", approval="PENDING", status="PENDING_APPROVAL")

        with patch(
            "miya.services.ops.invoices.require_restaurant", return_value=None
        ), patch(
            "miya.services.ops.invoices.require_permission", return_value=None
        ), patch(
            "miya.services.ops.invoices.resolve_invoice", return_value=(inv, None)
        ), patch(
            "finance.payment_approval.payment_allowed",
            return_value=(False, "Still waiting on PayGuard."),
        ):
            blocked = mark_invoice_paid(ctx, invoice_id="inv-abc-1")
        self.assertFalse(blocked.success)
        self.assertEqual(blocked.code, "approval_required")

        inv2 = _invoice(amount="400", approval="APPROVED", status="APPROVED")
        inv2.lifecycle_status = "APPROVED"

        def _mark_paid(**kwargs):
            inv2.status = Invoice.STATUS_PAID
            inv2.lifecycle_status = Invoice.STATUS_PAID

        inv2.mark_paid = MagicMock(side_effect=_mark_paid)
        inv2.save = MagicMock()

        with patch(
            "miya.services.ops.invoices.require_restaurant", return_value=None
        ), patch(
            "miya.services.ops.invoices.require_permission", return_value=None
        ), patch(
            "miya.services.ops.invoices.resolve_invoice", return_value=(inv2, None)
        ), patch(
            "finance.payment_approval.payment_allowed", return_value=(True, "")
        ), patch(
            "finance.audit.log_invoice_event"
        ), patch(
            "finance.models.Invoice.objects"
        ) as mock_objs:
            fresh = _invoice(amount="400", status="PAID", approval="APPROVED")
            fresh.status = Invoice.STATUS_PAID
            fresh.lifecycle_status = Invoice.STATUS_PAID
            mock_objs.filter.return_value.first.return_value = fresh
            paid = mark_invoice_paid(ctx, invoice_id="inv-abc-1", method="BANK_TRANSFER")
        self.assertTrue(paid.success)
        self.assertTrue(paid.verified)

    def test_attach_proof(self):
        from miya.services.ops.invoices import attach_invoice_proof

        ctx = _ctx()
        inv = _invoice(amount="400", status="PAID", approval="APPROVED")
        inv.proof_of_payment = MagicMock()
        inv.proof_of_payment.name = ""
        inv.proof_of_payment.save = MagicMock(
            side_effect=lambda *a, **k: setattr(inv.proof_of_payment, "name", "proofs/x.jpg")
        )
        inv.refresh_from_db = MagicMock()

        with patch(
            "miya.services.ops.invoices.require_restaurant", return_value=None
        ), patch(
            "miya.services.ops.invoices.require_permission", return_value=None
        ), patch(
            "miya.services.ops.invoices.resolve_invoice", return_value=(inv, None)
        ), patch(
            "finance.audit.log_invoice_event"
        ):
            # After save, name is set
            result = attach_invoice_proof(
                ctx, invoice_id="inv-abc-1", file_bytes=b"\xff\xd8proof"
            )
        self.assertTrue(result.success)
        self.assertTrue(result.data.get("proof_attached"))

    def test_what_happened_timeline_live(self):
        from miya.services.ops.invoices import get_invoice_timeline

        ctx = _ctx()
        inv = _invoice(amount="1250", vendor="ABC Foods", status="PAID", approval="APPROVED")
        inv.lifecycle_status = "PAID"
        events = [
            {
                "event_type": "CREATED",
                "summary": "Invoice created from OCR.",
                "at": "2026-08-06T10:00:00+00:00",
            },
            {
                "event_type": "APPROVED",
                "summary": "PayGuard approved.",
                "at": "2026-08-06T12:00:00+00:00",
            },
            {
                "event_type": "PAYMENT_RECORDED",
                "summary": "Payment recorded.",
                "at": "2026-08-07T09:00:00+00:00",
            },
        ]
        summary = (
            "Invoice #AF-100 — ABC Foods: 1250 MAD. Status: PAID.\n"
            "- Created\n- Approved\n- Payment recorded"
        )
        with patch(
            "miya.services.ops.invoices.require_restaurant", return_value=None
        ), patch(
            "miya.services.ops.invoices.require_permission", return_value=None
        ), patch(
            "miya.services.ops.invoices.resolve_invoice", return_value=(inv, None)
        ), patch(
            "finance.models.Invoice.objects"
        ) as mock_objs, patch(
            "finance.timeline.build_invoice_timeline", return_value=events
        ), patch(
            "finance.timeline.summarize_timeline_for_miya", return_value=summary
        ):
            mock_objs.filter.return_value.select_related.return_value.first.return_value = inv
            result = get_invoice_timeline(ctx, vendor="ABC Foods")
        self.assertTrue(result.success)
        self.assertIn("ABC Foods", result.message_for_user)
        self.assertIn("PAID", result.message_for_user)
        self.assertEqual(result.data.get("lifecycle_status"), "PAID")
        self.assertEqual(len(result.data.get("events") or []), 3)


class AmbiguityAndDispatchTests(SimpleTestCase):
    def test_clarify_multiple_vendor_matches(self):
        from miya.services.ops.invoices import resolve_invoice
        from finance.models import Invoice

        ctx = _ctx()
        a = _invoice(vendor="ABC Foods", amount="100")
        a.id = "inv-1"
        b = _invoice(vendor="ABC Foods", amount="200")
        b.id = "inv-2"
        with patch("finance.models.Invoice.objects") as mock_objs:
            qs = MagicMock()
            qs.exclude.return_value = qs
            qs.filter.return_value = qs
            qs.order_by.return_value = [a, b]
            mock_objs.filter.return_value = qs
            inv, err = resolve_invoice(ctx, vendor="ABC Foods")
        self.assertIsNone(inv)
        self.assertTrue(err.needs_clarification)
        self.assertEqual(len(err.data.get("invoices") or []), 2)

    def test_canonical_tools_registered(self):
        for name in (
            "find_invoices",
            "get_invoice",
            "get_invoice_timeline",
            "record_invoice",
            "payment_approval",
            "check_invoice_approval",
            "mark_invoice_paid",
            "attach_invoice_proof",
            "return_invoice",
        ):
            self.assertIn(name, CANONICAL_TOOL_NAMES)

    def test_dispatch_timeline(self):
        ctx = _ctx()
        with patch("miya.services.ops.invoices.get_invoice_timeline") as mock_fn:
            from miya.services.ops.result import ok

            mock_fn.return_value = ok(
                message="Invoice — ABC Foods: PAID.",
                verified=True,
                data={"lifecycle_status": "PAID"},
            )
            result = dispatch_canonical_tool(
                "get_invoice_timeline",
                {"vendor": "ABC Foods"},
                ctx=ctx,
            )
        self.assertTrue(result.success)
        self.assertIn("ABC Foods", result.message_for_user)
