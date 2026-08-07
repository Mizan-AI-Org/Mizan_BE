"""Phase 6: document OCR → structured fields → Miya retrieval."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from miya.services.document_intelligence import (
    document_matches_query,
    normalize_structured_fields,
)
from miya.services.ops import CANONICAL_TOOL_NAMES, dispatch_canonical_tool
from miya.services.ops.context import OpsContext


def _ctx(*, channel="dashboard"):
    rest = MagicMock()
    rest.id = "rest-1"
    user = MagicMock()
    user.id = "mgr-1"
    user.pk = "mgr-1"
    user.role = "MANAGER"
    user.phone = "212600000001"
    return OpsContext(
        user=user,
        restaurant=rest,
        restaurant_id="rest-1",
        user_id="mgr-1",
        role="MANAGER",
        channel=channel,
    )


class StructuredExtractionTests(SimpleTestCase):
    def test_normalize_invoice_fields(self):
        structured = normalize_structured_fields(
            {"category": "invoice_or_receipt", "confidence": 0.9, "fields": {}},
            fields={
                "vendor": "Metro Cash",
                "amount": "1,250.50",
                "currency": "mad",
                "invoice_number": "INV-9",
                "due_date": "2026-09-01",
            },
            category="invoice_or_receipt",
            title="Invoice Metro",
        )
        self.assertEqual(structured["vendor"], "Metro Cash")
        self.assertEqual(structured["amount"], "1250.50")
        self.assertEqual(structured["currency"], "MAD")
        self.assertEqual(structured["due_date"], "2026-09-01")

    def test_normalize_insurance_expiry(self):
        structured = normalize_structured_fields(
            {"fields": {"expiry_date": "2027-03-15", "document_type": "INSURANCE"}},
            category="id_or_certification",
            title="Restaurant insurance",
        )
        self.assertEqual(structured["expiry_date"], "2027-03-15")

    def test_search_matches_structured_vendor(self):
        self.assertTrue(
            document_matches_query(
                title="Scan",
                summary="",
                structured={"vendor": "Metro Cash", "amount": "100"},
                q="metro",
            )
        )


class QueryIntelligenceTests(SimpleTestCase):
    def test_insurance_expiry_from_compliance(self):
        from miya.services.ops.documents import query_document_intelligence

        ctx = _ctx()
        compliance_row = {
            "id": "c1",
            "kind": "compliance",
            "title": "Liability insurance",
            "document_type": "INSURANCE",
            "expiry_date": "2027-06-30",
            "structured": {"expiry_date": "2027-06-30", "document_type": "INSURANCE"},
        }
        with patch(
            "miya.services.ops.documents.require_restaurant", return_value=None
        ), patch(
            "miya.services.ops.documents.find_documents"
        ) as mock_find:
            from miya.services.ops.result import ok

            mock_find.return_value = ok(
                message="found",
                verified=True,
                data={"documents": [compliance_row], "count": 1},
            )
            result = query_document_intelligence(
                ctx, question="When does our insurance expire?"
            )
        self.assertTrue(result.success)
        self.assertIn("2027-06-30", result.message_for_user)

    def test_invoice_amount_and_supplier(self):
        from miya.services.ops.documents import query_document_intelligence

        ctx = _ctx()
        inv = {
            "id": "inv1",
            "kind": "invoice",
            "title": "Invoice — Metro Cash",
            "vendor": "Metro Cash",
            "amount": "1250.50",
            "currency": "MAD",
            "structured": {
                "vendor": "Metro Cash",
                "amount": "1250.50",
                "currency": "MAD",
            },
        }
        with patch(
            "miya.services.ops.documents.require_restaurant", return_value=None
        ), patch(
            "miya.services.ops.documents.find_documents"
        ) as mock_find:
            from miya.services.ops.result import ok

            mock_find.return_value = ok(
                message="found", verified=True, data={"documents": [inv], "count": 1}
            )
            amt = query_document_intelligence(
                ctx, question="What is the amount on this invoice?", q="invoice"
            )
            vendor = query_document_intelligence(
                ctx, question="What supplier is on this invoice?", q="invoice"
            )
        self.assertTrue(amt.success)
        self.assertIn("1250.50", amt.message_for_user)
        self.assertTrue(vendor.success)
        self.assertIn("Metro Cash", vendor.message_for_user)

    def test_yesterday_invoice_upload(self):
        from miya.services.ops.documents import query_document_intelligence

        ctx = _ctx()
        inv = {
            "id": "inv2",
            "kind": "invoice",
            "title": "Invoice — Sysco",
            "status": "OPEN",
            "vendor": "Sysco",
            "amount": "400",
            "structured": {"vendor": "Sysco", "amount": "400"},
        }
        with patch(
            "miya.services.ops.documents.require_restaurant", return_value=None
        ), patch(
            "miya.services.ops.documents.find_documents"
        ) as mock_find:
            from miya.services.ops.result import ok

            mock_find.return_value = ok(
                message="found", verified=True, data={"documents": [inv], "count": 1}
            )
            result = query_document_intelligence(
                ctx,
                question="What happened with the invoice we uploaded yesterday?",
            )
        self.assertTrue(result.success)
        self.assertIn("Sysco", result.message_for_user)
        self.assertEqual(mock_find.call_args.kwargs.get("since"), "yesterday")


class SerializeAndShowTests(SimpleTestCase):
    def test_serialize_exposes_structured(self):
        from miya.services.tenant_documents import serialize_tenant_document

        doc = MagicMock()
        doc.id = "d1"
        doc.title = "Insurance policy"
        doc.category = "id_or_certification"
        doc.summary = "Policy"
        doc.original_filename = "ins.pdf"
        doc.mime_type = "application/pdf"
        doc.source = "WIDGET"
        doc.file = None
        doc.file_url = "https://cdn.example/ins.pdf"
        doc.uploaded_by = None
        doc.uploader_phone = ""
        doc.created_at = None
        doc.compliance_document_id = None
        doc.invoice_id = None
        doc.tags = ["insurance"]
        doc.structured_fields = {
            "vendor": None,
            "expiry_date": "2027-01-01",
            "document_type": "INSURANCE",
        }
        doc.vendor_name = ""
        doc.amount = None
        doc.currency = ""
        doc.invoice_number = ""
        doc.expiry_date = None
        doc.extracted_text = ""
        doc.parse_metadata = {}

        # Clean None vendor from structured for serialize path
        doc.structured_fields = {
            "expiry_date": "2027-01-01",
            "document_type": "INSURANCE",
        }
        row = serialize_tenant_document(doc)
        self.assertEqual(row["expiry_date"], "2027-01-01")
        self.assertEqual(row["structured"]["expiry_date"], "2027-01-01")
        self.assertTrue(row["file_url"])

    def test_show_document_dashboard_ref(self):
        from miya.services.ops.documents import show_document
        from miya.services.ops.result import ok

        ctx = _ctx(channel="dashboard")
        detail = {
            "id": "d1",
            "kind": "tenant_file",
            "title": "Insurance policy",
            "file_url": "https://cdn.example/ins.pdf",
            "structured": {"expiry_date": "2027-01-01"},
        }
        with patch(
            "miya.services.ops.documents.get_document",
            return_value=ok(message="ok", verified=True, data={"document": detail}),
        ):
            result = show_document(ctx, q="insurance")
        self.assertTrue(result.success)
        self.assertFalse(result.data.get("whatsapp_file_sent"))
        self.assertTrue(result.data.get("secure_document_ref", {}).get("has_secure_url"))


class CanonicalDispatchPhase6Tests(SimpleTestCase):
    def test_tools_registered(self):
        for name in (
            "find_documents",
            "get_document",
            "show_document",
            "query_document_intelligence",
            "list_tenant_documents",
            "get_tenant_document",
            "find_invoices",
        ):
            self.assertIn(name, CANONICAL_TOOL_NAMES)

    def test_dispatch_query_intelligence(self):
        ctx = _ctx()
        with patch(
            "miya.services.ops.documents.query_document_intelligence"
        ) as mock_fn:
            from miya.services.ops.result import ok

            mock_fn.return_value = ok(
                message="Expires on 2027-06-30.",
                verified=True,
                data={"structured": {"expiry_date": "2027-06-30"}},
            )
            result = dispatch_canonical_tool(
                "query_document_intelligence",
                {"question": "When does our insurance expire?"},
                ctx=ctx,
            )
        self.assertTrue(result.success)
        self.assertIn("2027-06-30", result.message_for_user)
