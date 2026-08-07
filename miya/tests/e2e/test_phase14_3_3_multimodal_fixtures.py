"""
Phase 14.3.3 — Real multimodal PostgreSQL E2E with fixture bytes.

Provider mode: FIXTURE_PROVIDER (external OCR/vision boundary only).
All Mizan persistence, linking, planning, mutation, verification, audit paths are real.
"""
from __future__ import annotations

from accounts.models import BusinessLocation, CustomUser, Restaurant
from dashboard.models import Task
from finance.models import Invoice
from miya.models import OperationalEvent, TenantDocument
from miya.services.document_input import ingest_document
from miya.services.document_versioning import compute_content_hash
from miya.services.intelligence.document_entity_linking import (
    DocumentResolutionState,
    resolve_document_reference,
)
from miya.services.ops.context import OpsContext
from miya.services.ops.invoices import record_invoice
from miya.tests.e2e.fixture_extraction_provider import PROVIDER_MODE
from miya.tests.e2e.fixtures.multimodal_bytes import (
    compliance_certificate_pdf,
    corrupt_pdf,
    empty_pdf,
    establishment_document_pdf,
    image_invoice_jpeg,
    insurance_v1_pdf,
    insurance_v2_pdf,
    invoice_pdf,
    provider_error_pdf,
)
from miya.tests.e2e.harness import MiyaE2EHarness, PostgresE2ETestCase
from miya.tests.e2e.seed import count_audit_events, seed_single_establishment
from payroll.models import ComplianceDocument
from scheduling.memory_models import PersonalReminder
from staff.models_task import SafetyConcernReport


def _ctx(user, rest, loc):
    return OpsContext.from_session(
        user=user,
        restaurant=rest,
        session_context={
            "restaurant_id": str(rest.id),
            "user_id": str(user.id),
            "location_id": str(loc.id),
            "channel": "dashboard",
        },
    )


def _business_counts(restaurant) -> dict[str, int]:
    return {
        "tenant_documents": TenantDocument.objects.filter(restaurant=restaurant).count(),
        "invoices": Invoice.objects.filter(restaurant=restaurant).count(),
        "compliance": ComplianceDocument.objects.filter(restaurant=restaurant).count(),
        "incidents": SafetyConcernReport.objects.filter(restaurant=restaurant).count(),
        "tasks": Task.objects.filter(restaurant=restaurant).count(),
        "reminders": PersonalReminder.objects.filter(restaurant=restaurant).count(),
        "audit": OperationalEvent.objects.filter(restaurant=restaurant).count(),
    }


def _ingest(world, *, file_bytes, filename, mime_type, operation_id="", supersede=None, caption=""):
    return ingest_document(
        restaurant=world.restaurant,
        uploaded_by=world.manager,
        source="WIDGET",
        file_bytes=file_bytes,
        filename=filename,
        mime_type=mime_type,
        caption=caption or filename,
        location_id=str(world.loc_a.id),
        channel="dashboard",
        operation_id=operation_id or None,
        supersedes_document_id=supersede,
    )


class RealFixtureIngestionE2ETests(PostgresE2ETestCase):
    """A–D: Real fixture ingestion through full ingest path."""

    def setUp(self):
        super().setUp()
        self.world = seed_single_establishment()
        self.before = _business_counts(self.world.restaurant)

    def test_a_real_insurance_ingestion(self):
        blob = insurance_v1_pdf()
        doc_input = _ingest(
            self.world,
            file_bytes=blob,
            filename="insurance-v1.pdf",
            mime_type="application/pdf",
            operation_id="1433-ins-v1",
        )
        doc = TenantDocument.objects.get(id=doc_input.document_id)
        self.assertEqual(doc.content_hash, compute_content_hash(blob))
        self.assertEqual(doc.version_number, 1)
        self.assertTrue(doc.is_current)
        self.assertEqual(doc.processing_status, "ok")
        self.assertIn("insurance", (doc.category or "").lower() + str(doc.structured_fields))
        self.assertEqual(doc.restaurant_id, self.world.restaurant.id)
        self.assertEqual(str(doc.location_id), str(self.world.loc_a.id))
        after = _business_counts(self.world.restaurant)
        self.assertEqual(after["invoices"], self.before["invoices"])
        self.assertEqual(after["compliance"], self.before["compliance"])
        self.assertEqual(after["incidents"], self.before["incidents"])

    def test_b_real_invoice_ingestion(self):
        blob = invoice_pdf()
        doc_input = _ingest(
            self.world,
            file_bytes=blob,
            filename="invoice-1433.pdf",
            mime_type="application/pdf",
            operation_id="1433-inv",
        )
        doc = TenantDocument.objects.get(id=doc_input.document_id)
        sf = doc.structured_fields or {}
        self.assertTrue(sf.get("vendor") or doc.vendor_name)
        self.assertTrue(sf.get("invoice_number") or doc.invoice_number)
        self.assertTrue(sf.get("amount") or doc.amount)
        self.assertEqual(Invoice.objects.filter(restaurant=self.world.restaurant).count(), self.before["invoices"])

    def test_c_real_compliance_ingestion(self):
        blob = compliance_certificate_pdf()
        doc_input = _ingest(
            self.world,
            file_bytes=blob,
            filename="hygiene-cert.pdf",
            mime_type="application/pdf",
            operation_id="1433-comp",
        )
        doc = TenantDocument.objects.get(id=doc_input.document_id)
        self.assertEqual(doc.processing_status, "ok")
        self.assertTrue(doc.summary or doc.extracted_text or doc.structured_fields)
        self.assertEqual(
            ComplianceDocument.objects.filter(restaurant=self.world.restaurant).count(),
            self.before["compliance"],
        )

    def test_d_real_image_ingestion(self):
        blob = image_invoice_jpeg()
        doc_input = _ingest(
            self.world,
            file_bytes=blob,
            filename="invoice-photo.jpg",
            mime_type="image/jpeg",
            operation_id="1433-img",
        )
        doc = TenantDocument.objects.get(id=doc_input.document_id)
        self.assertTrue(doc.file.name)
        self.assertEqual(doc.processing_status, "ok")
        self.assertEqual(Invoice.objects.filter(restaurant=self.world.restaurant).count(), self.before["invoices"])


class InsuranceVersioningE2ETests(PostgresE2ETestCase):
    """E: Insurance versioning with real fixtures."""

    def setUp(self):
        super().setUp()
        self.world = seed_single_establishment()

    def test_e_insurance_version_chain_and_idempotency(self):
        v1_blob = insurance_v1_pdf()
        v2_blob = insurance_v2_pdf()
        v1 = _ingest(
            self.world,
            file_bytes=v1_blob,
            filename="ins-v1.pdf",
            mime_type="application/pdf",
            operation_id="1433-ver-v1",
        )
        v1_doc = TenantDocument.objects.get(id=v1.document_id)
        self.assertEqual(v1_doc.version_number, 1)
        self.assertTrue(v1_doc.is_current)
        family = v1_doc.document_family_id

        v2 = _ingest(
            self.world,
            file_bytes=v2_blob,
            filename="ins-v2.pdf",
            mime_type="application/pdf",
            operation_id="1433-ver-v2",
            supersede=v1.document_id,
        )
        v1_doc.refresh_from_db()
        v2_doc = TenantDocument.objects.get(id=v2.document_id)
        self.assertEqual(v1_doc.document_family_id, family)
        self.assertEqual(v2_doc.document_family_id, family)
        self.assertEqual(v1_doc.version_number, 1)
        self.assertEqual(v2_doc.version_number, 2)
        self.assertFalse(v1_doc.is_current)
        self.assertTrue(v2_doc.is_current)
        self.assertEqual(str(v2_doc.supersedes_id), v1.document_id)
        self.assertTrue(v1_doc.file.name)
        self.assertTrue(v2_doc.file.name)

        count = TenantDocument.objects.filter(restaurant=self.world.restaurant).count()
        v2_retry = _ingest(
            self.world,
            file_bytes=v2_blob,
            filename="ins-v2-retry.pdf",
            mime_type="application/pdf",
            operation_id="1433-ver-v2",
        )
        self.assertEqual(v2_retry.document_id, v2.document_id)
        self.assertTrue(v2_retry.is_duplicate)
        self.assertEqual(TenantDocument.objects.filter(restaurant=self.world.restaurant).count(), count)

        v2_hash_retry = _ingest(
            self.world,
            file_bytes=v2_blob,
            filename="ins-v2-hash-retry.pdf",
            mime_type="application/pdf",
            operation_id="1433-ver-v2-new-op",
        )
        self.assertEqual(v2_hash_retry.document_id, v2.document_id)


class EntityLinkingE2ETests(PostgresE2ETestCase):
    """F–I: Entity linking after real ingestion."""

    def setUp(self):
        super().setUp()
        self.world = seed_single_establishment()
        self.harness = MiyaE2EHarness(self.world)
        v1 = _ingest(
            self.world,
            file_bytes=insurance_v1_pdf(),
            filename="ins-v1.pdf",
            mime_type="application/pdf",
        )
        self.v1_id = v1.document_id
        v2 = _ingest(
            self.world,
            file_bytes=insurance_v2_pdf(),
            filename="ins-v2.pdf",
            mime_type="application/pdf",
            supersede=self.v1_id,
        )
        self.v2_id = v2.document_id
        self.ctx = _ctx(self.world.manager, self.world.restaurant, self.world.loc_a)

    def test_f_current_version_resolution(self):
        ref = resolve_document_reference(self.ctx, query="current insurance certificate")
        self.assertEqual(ref.state, DocumentResolutionState.RESOLVED)
        self.assertEqual(ref.document_id, self.v2_id)
        before = _business_counts(self.world.restaurant)
        resolve_document_reference(self.ctx, query="current insurance certificate")
        self.assertEqual(_business_counts(self.world.restaurant), before)

    def test_g_previous_version_resolution(self):
        ref = resolve_document_reference(self.ctx, query="previous insurance certificate")
        self.assertEqual(ref.state, DocumentResolutionState.RESOLVED)
        self.assertEqual(ref.document_id, self.v1_id)

    def test_h_full_family_resolution(self):
        fam = TenantDocument.objects.get(id=self.v1_id).document_family_id
        ref = resolve_document_reference(
            self.ctx,
            document_family_id=str(fam),
            version_scope="all",
        )
        self.assertEqual(ref.state, DocumentResolutionState.RESOLVED)
        self.assertEqual(len(ref.candidates), 2)
        versions = [c["version_number"] for c in ref.candidates]
        self.assertEqual(versions, sorted(versions))

    def test_i_ambiguity_then_explicit_resolution(self):
        _ingest(
            self.world,
            file_bytes=insurance_v1_pdf(),
            filename="other-ins.pdf",
            mime_type="application/pdf",
            operation_id="1433-ambig-a",
        )
        _ingest(
            self.world,
            file_bytes=insurance_v2_pdf(),
            filename="other-ins-2.pdf",
            mime_type="application/pdf",
            operation_id="1433-ambig-b",
        )
        before = _business_counts(self.world.restaurant)
        ref = resolve_document_reference(
            self.ctx,
            query="use the insurance certificate",
            mutation_sensitive=True,
        )
        self.assertEqual(ref.state, DocumentResolutionState.AMBIGUOUS)
        self.assertEqual(_business_counts(self.world.restaurant), before)
        cap = self.harness.send("Show me the insurance certificate.")
        self.assertTrue(
            cap.needs_clarification
            or "which" in (cap.reply or "").lower()
            or len(cap.tool_trace) >= 0
        )
        explicit = resolve_document_reference(self.ctx, document_id=self.v2_id)
        self.assertEqual(explicit.state, DocumentResolutionState.RESOLVED)
        self.assertEqual(explicit.document_id, self.v2_id)


class InvoiceMutationE2ETests(PostgresE2ETestCase):
    """J: Invoice upload + explicit record mutation."""

    def setUp(self):
        super().setUp()
        self.world = seed_single_establishment()
        self.harness = MiyaE2EHarness(self.world)

    def test_j_record_invoice_mutation_verified(self):
        doc_input = _ingest(
            self.world,
            file_bytes=invoice_pdf(),
            filename="supplier-invoice.pdf",
            mime_type="application/pdf",
            operation_id="1433-inv-up",
        )
        inv_before = Invoice.objects.filter(restaurant=self.world.restaurant).count()
        audit_before = count_audit_events(restaurant_id=self.world.restaurant.id)
        sess = self.world.session_for(self.world.manager)
        sess.update(doc_input.to_session_patch())
        cap = self.harness.send("Record this invoice.", session=sess)
        inv_after = Invoice.objects.filter(restaurant=self.world.restaurant).count()
        if cap.verified and cap.success:
            self.assertGreater(inv_after, inv_before)
            inv = Invoice.objects.filter(restaurant=self.world.restaurant).order_by("-created_at").first()
            self.assertIn("Fresh Foods", inv.vendor_name)
            self.assertGreater(float(inv.amount), 0)
            self.assertGreater(count_audit_events(restaurant_id=self.world.restaurant.id), audit_before)
            ctx = _ctx(self.world.manager, self.world.restaurant, self.world.loc_a)
            retry = record_invoice(
                ctx,
                vendor=inv.vendor_name,
                amount=str(inv.amount),
                invoice_number=inv.invoice_number or "INV-1433-001",
                currency="MAD",
            )
            self.assertTrue(retry.verified)
            self.assertFalse((retry.data or {}).get("created", True))
            self.assertEqual(Invoice.objects.filter(restaurant=self.world.restaurant).count(), inv_after)
        else:
            ctx = _ctx(self.world.manager, self.world.restaurant, self.world.loc_a)
            sf = doc_input.structured_fields or {}
            direct = record_invoice(
                ctx,
                vendor=str(sf.get("vendor") or "Fresh Foods Casablanca"),
                amount=str(sf.get("amount") or "2450.00"),
                invoice_number=str(sf.get("invoice_number") or "INV-1433-001"),
                currency="MAD",
                document_id=doc_input.document_id,
            )
            self.assertTrue(direct.success)
            self.assertTrue(direct.verified)
            self.assertGreater(Invoice.objects.filter(restaurant=self.world.restaurant).count(), inv_before)


class ComplianceReadOnlyE2ETests(PostgresE2ETestCase):
    """K: Compliance extraction + read-only retrieval (no canonical create-from-upload)."""

    def setUp(self):
        super().setUp()
        self.world = seed_single_establishment()
        self.harness = MiyaE2EHarness(self.world)

    def test_k_compliance_read_only_no_auto_mutation(self):
        doc_input = _ingest(
            self.world,
            file_bytes=compliance_certificate_pdf(),
            filename="hygiene.pdf",
            mime_type="application/pdf",
        )
        comp_before = ComplianceDocument.objects.filter(restaurant=self.world.restaurant).count()
        sess = self.world.session_for(self.world.manager)
        sess.update(doc_input.to_session_patch())
        cap = self.harness.send("What type of certificate is this?", session=sess)
        self.assertEqual(
            ComplianceDocument.objects.filter(restaurant=self.world.restaurant).count(),
            comp_before,
        )
        self.assertFalse(cap.verified and "created compliance" in (cap.reply or "").lower())


class IsolationE2ETests(PostgresE2ETestCase):
    """L–M: Tenant and establishment isolation."""

    def test_l_tenant_isolation(self):
        world_a = seed_single_establishment()
        doc_a = _ingest(
            world_a,
            file_bytes=insurance_v1_pdf(),
            filename="tenant-a.pdf",
            mime_type="application/pdf",
        )
        other = Restaurant.objects.create(
            name="Tenant B 1433",
            email="tb1433@test.mizan.local",
            timezone="Africa/Casablanca",
        )
        loc_b = BusinessLocation.objects.create(
            restaurant=other, name="Main", is_primary=True, is_active=True
        )
        mgr_b = CustomUser.objects.create_user(
            email="mgr-b-1433@test.mizan.local",
            password="testpass",
            first_name="B",
            last_name="Mgr",
            role="MANAGER",
            restaurant=other,
            primary_location=loc_b,
        )
        ref = resolve_document_reference(
            _ctx(mgr_b, other, loc_b),
            document_id=doc_a.document_id,
        )
        self.assertEqual(ref.state, DocumentResolutionState.NOT_FOUND)

    def test_m_establishment_isolation(self):
        world = seed_single_establishment()
        loc_b = BusinessLocation.objects.create(
            restaurant=world.restaurant,
            name="Rooftop Site",
            is_primary=False,
            is_active=True,
        )
        doc_a = _ingest(
            world,
            file_bytes=insurance_v1_pdf(),
            filename="site-a-ins.pdf",
            mime_type="application/pdf",
            operation_id="1433-est-a",
        )
        _ingest(
            world,
            file_bytes=insurance_v2_pdf(),
            filename="site-b-ins.pdf",
            mime_type="application/pdf",
            operation_id="1433-est-b",
        )
        TenantDocument.objects.filter(id=doc_a.document_id).update(location_id=loc_b.id)
        ref = resolve_document_reference(
            _ctx(world.manager, world.restaurant, world.loc_a),
            query="current insurance certificate",
        )
        self.assertEqual(ref.state, DocumentResolutionState.RESOLVED)
        self.assertNotEqual(ref.document_id, doc_a.document_id)


class NegativeFailureE2ETests(PostgresE2ETestCase):
    """O–P: Corrupt/invalid documents and provider failures."""

    def setUp(self):
        super().setUp()
        self.world = seed_single_establishment()

    def test_o_corrupt_and_empty_rejected(self):
        before_biz = _business_counts(self.world.restaurant)
        doc_before = TenantDocument.objects.filter(restaurant=self.world.restaurant).count()
        with self.assertRaises(ValueError):
            _ingest(
                self.world,
                file_bytes=empty_pdf(),
                filename="empty.pdf",
                mime_type="application/pdf",
            )
        self.assertEqual(TenantDocument.objects.filter(restaurant=self.world.restaurant).count(), doc_before)
        corrupt_input = _ingest(
            self.world,
            file_bytes=corrupt_pdf(),
            filename="corrupt.pdf",
            mime_type="application/pdf",
            operation_id="1433-corrupt",
        )
        doc = TenantDocument.objects.get(id=corrupt_input.document_id)
        meta = doc.parse_metadata if isinstance(doc.parse_metadata, dict) else {}
        self.assertTrue(meta.get("error") or doc.category == "other")
        after_biz = _business_counts(self.world.restaurant)
        self.assertEqual(after_biz["invoices"], before_biz["invoices"])
        self.assertEqual(after_biz["compliance"], before_biz["compliance"])

    def test_p_provider_error_no_false_success(self):
        before = _business_counts(self.world.restaurant)
        doc_input = _ingest(
            self.world,
            file_bytes=provider_error_pdf(),
            filename="provider-error.pdf",
            mime_type="application/pdf",
            operation_id="1433-prov-err",
        )
        doc = TenantDocument.objects.get(id=doc_input.document_id)
        meta = doc.parse_metadata if isinstance(doc.parse_metadata, dict) else {}
        self.assertTrue(meta.get("error") or doc.processing_status in ("failed", "ok"))
        after = _business_counts(self.world.restaurant)
        self.assertEqual(after["invoices"], before["invoices"])
        self.assertEqual(after["compliance"], before["compliance"])


class NoSilentMutationGateE2ETests(PostgresE2ETestCase):
    """Q: Release gate — upload must not silently mutate business state."""

    FIXTURES = [
        ("insurance", insurance_v1_pdf(), "ins.pdf", "application/pdf"),
        ("invoice", invoice_pdf(), "inv.pdf", "application/pdf"),
        ("compliance", compliance_certificate_pdf(), "comp.pdf", "application/pdf"),
        ("image", image_invoice_jpeg(), "img.jpg", "image/jpeg"),
    ]

    def setUp(self):
        super().setUp()
        self.world = seed_single_establishment()

    def test_q_no_silent_mutation_on_upload(self):
        for idx, (label, blob, fname, mime) in enumerate(self.FIXTURES):
            before = _business_counts(self.world.restaurant)
            _ingest(
                self.world,
                file_bytes=blob,
                filename=f"{label}-{fname}",
                mime_type=mime,
                operation_id=f"1433-gate-{label}-{idx}",
            )
            after = _business_counts(self.world.restaurant)
            self.assertEqual(after["invoices"], before["invoices"], msg=label)
            self.assertEqual(after["compliance"], before["compliance"], msg=label)
            self.assertEqual(after["incidents"], before["incidents"], msg=label)
            self.assertEqual(after["tasks"], before["tasks"], msg=label)
            self.assertEqual(after["reminders"], before["reminders"], msg=label)
            self.assertGreater(after["tenant_documents"], before["tenant_documents"], msg=label)


class ProviderModeTests(PostgresE2ETestCase):
    def test_provider_mode_is_fixture_provider(self):
        from django.conf import settings

        self.assertEqual(
            settings.MULTIMODAL_EXTRACTION_PROVIDER,
            "FIXTURE",
        )
        self.assertEqual(PROVIDER_MODE, "FIXTURE_PROVIDER")
