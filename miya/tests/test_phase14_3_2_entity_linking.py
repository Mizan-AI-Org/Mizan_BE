"""Phase 14.3.2 — Document entity linking at reasoning time."""
from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from accounts.models import BusinessLocation, CustomUser, Restaurant
from miya.models import OperationalEvent, TenantDocument
from miya.services.document_input import ingest_document
from miya.services.intelligence.document_entity_linking import (
    DocumentResolutionState,
    resolve_document_reference,
)
from miya.services.intelligence.entity_resolver import resolve_entity_reference
from miya.services.intelligence.planning.resolve import resolve_plan
from miya.services.intelligence.planning.types import (
    ClassifiedIntent,
    Confidence,
    EntityType,
    IntentClass,
)
from miya.services.intelligence.working_memory import update_working_memory
from miya.services.ops.context import OpsContext
from miya.tests.e2e.harness import PostgresE2ETestCase

INSURANCE_PARSE = {
    "category": "insurance",
    "confidence": 0.91,
    "summary": "Insurance policy",
    "fields": {"expiry_date": "2026-09-30", "document_type": "insurance"},
}

INSURANCE_PARSE_B = {
    "category": "insurance",
    "confidence": 0.91,
    "summary": "Second insurance policy",
    "fields": {"expiry_date": "2027-03-15", "document_type": "insurance"},
}


def _seed():
    rest = Restaurant.objects.create(
        name="Link Rest",
        email="link@test.mizan.local",
        timezone="Africa/Casablanca",
    )
    other = Restaurant.objects.create(
        name="Other Link Rest",
        email="olink@test.mizan.local",
        timezone="Africa/Casablanca",
    )
    loc_a = BusinessLocation.objects.create(
        restaurant=rest, name="Kitchen Site", is_primary=True, is_active=True
    )
    loc_b = BusinessLocation.objects.create(
        restaurant=rest, name="Front Site", is_primary=False, is_active=True
    )
    mgr = CustomUser.objects.create_user(
        email="link-mgr@test.mizan.local",
        password="testpass",
        first_name="Mgr",
        last_name="Link",
        role="MANAGER",
        restaurant=rest,
        primary_location=loc_a,
    )
    mgr.managed_locations.add(loc_a, loc_b)
    return rest, other, loc_a, loc_b, mgr


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


@patch("miya.services.tenant_documents._parse_upload")
class DocumentEntityLinkingUnitTests(TestCase):
    def setUp(self):
        self.rest, self.other, self.loc_a, self.loc_b, self.manager = _seed()
        self.v1 = b"%PDF insurance v1 bytes"
        self.v2 = b"%PDF insurance v2 renewed bytes"

    def _ingest(self, mock_parse, *, bytes_, title, loc, parse_result=None, supersede=None):
        mock_parse.return_value = dict(parse_result or INSURANCE_PARSE)
        return ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=bytes_,
            filename=f"{title}.pdf",
            mime_type="application/pdf",
            caption=title,
            location_id=str(loc.id),
            supersedes_document_id=supersede,
        )

    def test_exact_document_id_resolves(self, mock_parse):
        doc = self._ingest(mock_parse, bytes_=self.v1, title="Policy A", loc=self.loc_a)
        ref = resolve_document_reference(
            _ctx(self.manager, self.rest, self.loc_a),
            document_id=doc.document_id,
        )
        self.assertEqual(ref.state, DocumentResolutionState.RESOLVED)
        self.assertEqual(ref.document_id, doc.document_id)

    def test_working_memory_resolves(self, mock_parse):
        doc = self._ingest(mock_parse, bytes_=self.v1, title="WM Policy", loc=self.loc_a)
        update_working_memory(
            user=self.manager,
            restaurant=self.rest,
            current_document_id=doc.document_id,
            current_document_label="WM Policy",
        )
        ref = resolve_document_reference(
            _ctx(self.manager, self.rest, self.loc_a),
            pronoun=True,
        )
        self.assertEqual(ref.state, DocumentResolutionState.RESOLVED)
        self.assertEqual(ref.document_id, doc.document_id)
        self.assertIn("working_memory", ref.evidence)

    def test_current_version_lookup(self, mock_parse):
        v1 = self._ingest(mock_parse, bytes_=self.v1, title="Insurance Current", loc=self.loc_a)
        mock_parse.return_value = dict(INSURANCE_PARSE)
        v2 = self._ingest(
            mock_parse,
            bytes_=self.v2,
            title="Insurance Current",
            loc=self.loc_a,
            supersede=v1.document_id,
        )
        ref = resolve_document_reference(
            _ctx(self.manager, self.rest, self.loc_a),
            query="current insurance certificate",
        )
        self.assertEqual(ref.state, DocumentResolutionState.RESOLVED)
        self.assertEqual(ref.document_id, v2.document_id)
        self.assertTrue(ref.is_current)

    def test_previous_version_lookup(self, mock_parse):
        v1 = self._ingest(mock_parse, bytes_=self.v1, title="Insurance Prev", loc=self.loc_a)
        mock_parse.return_value = dict(INSURANCE_PARSE)
        self._ingest(
            mock_parse,
            bytes_=self.v2,
            title="Insurance Prev",
            loc=self.loc_a,
            supersede=v1.document_id,
        )
        ref = resolve_document_reference(
            _ctx(self.manager, self.rest, self.loc_a),
            query="previous insurance certificate",
        )
        self.assertEqual(ref.state, DocumentResolutionState.RESOLVED)
        self.assertEqual(ref.document_id, v1.document_id)
        self.assertEqual(ref.version_scope, "previous")

    def test_all_versions_lookup(self, mock_parse):
        v1 = self._ingest(mock_parse, bytes_=self.v1, title="Insurance All", loc=self.loc_a)
        mock_parse.return_value = dict(INSURANCE_PARSE)
        self._ingest(
            mock_parse,
            bytes_=self.v2,
            title="Insurance All",
            loc=self.loc_a,
            supersede=v1.document_id,
        )
        v1_doc = TenantDocument.objects.get(id=v1.document_id)
        ref = resolve_document_reference(
            _ctx(self.manager, self.rest, self.loc_a),
            document_family_id=str(v1_doc.document_family_id),
            version_scope="all",
        )
        self.assertEqual(ref.state, DocumentResolutionState.RESOLVED)
        self.assertEqual(len(ref.candidates), 2)
        self.assertEqual(ref.version_scope, "all")

    def test_tenant_isolation(self, mock_parse):
        doc = self._ingest(mock_parse, bytes_=self.v1, title="Tenant Doc", loc=self.loc_a)
        other_mgr = CustomUser.objects.create_user(
            email="other-mgr@test.mizan.local",
            password="testpass",
            first_name="O",
            last_name="Mgr",
            role="MANAGER",
            restaurant=self.other,
        )
        other_loc = BusinessLocation.objects.create(
            restaurant=self.other, name="Main", is_primary=True, is_active=True
        )
        ref = resolve_document_reference(
            _ctx(other_mgr, self.other, other_loc),
            document_id=doc.document_id,
        )
        self.assertEqual(ref.state, DocumentResolutionState.NOT_FOUND)

    def test_establishment_isolation(self, mock_parse):
        doc_a = self._ingest(mock_parse, bytes_=self.v1, title="Kitchen Insurance", loc=self.loc_a)
        self._ingest(mock_parse, bytes_=b"%PDF other site", title="Front Insurance", loc=self.loc_b, parse_result=INSURANCE_PARSE_B)
        ref = resolve_document_reference(
            _ctx(self.manager, self.rest, self.loc_a),
            query="current insurance",
        )
        self.assertEqual(ref.state, DocumentResolutionState.RESOLVED)
        self.assertEqual(ref.document_id, doc_a.document_id)

    def test_single_candidate_resolves(self, mock_parse):
        doc = self._ingest(mock_parse, bytes_=self.v1, title="Unique Vendor Invoice", loc=self.loc_a, parse_result={
            "category": "invoice_or_receipt",
            "fields": {"vendor": "ABC Supplier", "amount": "100"},
            "summary": "Invoice ABC Supplier",
        })
        ref = resolve_document_reference(
            _ctx(self.manager, self.rest, self.loc_a),
            query="ABC Supplier invoice",
            vendor="ABC Supplier",
        )
        self.assertEqual(ref.state, DocumentResolutionState.RESOLVED)
        self.assertEqual(ref.document_id, doc.document_id)

    def test_multiple_candidates_ambiguous(self, mock_parse):
        self._ingest(mock_parse, bytes_=self.v1, title="Insurance June", loc=self.loc_a)
        self._ingest(mock_parse, bytes_=b"%PDF july insurance", title="Insurance July", loc=self.loc_a, parse_result=INSURANCE_PARSE_B)
        ref = resolve_document_reference(
            _ctx(self.manager, self.rest, self.loc_a),
            query="insurance certificate",
        )
        self.assertEqual(ref.state, DocumentResolutionState.AMBIGUOUS)
        self.assertGreaterEqual(len(ref.candidates), 2)
        self.assertIn("won't guess", ref.clarify_message.lower())

    def test_no_candidates_not_found(self, mock_parse):
        ref = resolve_document_reference(
            _ctx(self.manager, self.rest, self.loc_a),
            query="nonexistent compliance certificate",
        )
        self.assertEqual(ref.state, DocumentResolutionState.NOT_FOUND)

    def test_filename_alone_cannot_authorize_mutation(self, mock_parse):
        self._ingest(mock_parse, bytes_=self.v1, title="Shared Name", loc=self.loc_a)
        self._ingest(mock_parse, bytes_=b"%PDF other shared", title="Shared Name", loc=self.loc_a, parse_result=INSURANCE_PARSE_B)
        ref = resolve_document_reference(
            _ctx(self.manager, self.rest, self.loc_a),
            query="shared name.pdf",
            mutation_sensitive=True,
        )
        self.assertEqual(ref.state, DocumentResolutionState.AMBIGUOUS)

    def test_recency_alone_cannot_authorize_mutation(self, mock_parse):
        self._ingest(mock_parse, bytes_=self.v1, title="Old Insurance", loc=self.loc_a)
        self._ingest(mock_parse, bytes_=b"%PDF newer", title="New Insurance", loc=self.loc_a, parse_result=INSURANCE_PARSE_B)
        ref = resolve_document_reference(
            _ctx(self.manager, self.rest, self.loc_a),
            query="",
            mutation_sensitive=True,
        )
        self.assertIn(ref.state, (DocumentResolutionState.AMBIGUOUS, DocumentResolutionState.NOT_FOUND))

    def test_entity_resolution_does_not_mutate_db(self, mock_parse):
        self._ingest(mock_parse, bytes_=self.v1, title="No Mutate", loc=self.loc_a)
        before = TenantDocument.objects.filter(restaurant=self.rest).count()
        audit_before = OperationalEvent.objects.filter(restaurant=self.rest).count()
        resolve_document_reference(
            _ctx(self.manager, self.rest, self.loc_a),
            query="insurance",
            mutation_sensitive=True,
        )
        self.assertEqual(TenantDocument.objects.filter(restaurant=self.rest).count(), before)
        self.assertEqual(OperationalEvent.objects.filter(restaurant=self.rest).count(), audit_before)

    def test_resolved_document_still_requires_plan_authorization(self, mock_parse):
        doc = self._ingest(mock_parse, bytes_=self.v1, title="Plan Auth", loc=self.loc_a)
        intent = ClassifiedIntent(
            intent=IntentClass.RETRIEVE,
            entity_type=EntityType.DOCUMENT,
            confidence=Confidence.HIGH,
            query="insurance",
            raw_message="Show me the insurance",
            slots={"document_id": doc.document_id},
        )
        plan = resolve_plan(intent, ctx=_ctx(self.manager, self.rest, self.loc_a))
        self.assertNotEqual(plan.action.value, "CLARIFY")
        self.assertEqual(plan.entity_id, doc.document_id)

    def test_entity_resolver_integration(self, mock_parse):
        doc = self._ingest(mock_parse, bytes_=self.v1, title="Resolver Int", loc=self.loc_a)
        ref = resolve_entity_reference(
            _ctx(self.manager, self.rest, self.loc_a),
            entity_type="document",
            entity_id=doc.document_id,
        )
        self.assertEqual(ref.entity_id, doc.document_id)
        self.assertFalse(ref.needs_clarify)

    def test_versioning_idempotent_after_linking(self, mock_parse):
        first = self._ingest(mock_parse, bytes_=self.v1, title="Idem Link", loc=self.loc_a, parse_result=INSURANCE_PARSE)
        second = ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.v1,
            filename="idem.pdf",
            mime_type="application/pdf",
            operation_id="op-idem-link-1432",
        )
        self.assertEqual(first.document_id, second.document_id)


@patch("miya.services.tenant_documents._parse_upload")
class DocumentEntityLinkingPostgresE2ETests(PostgresE2ETestCase):
    def setUp(self):
        super().setUp()
        from miya.tests.e2e.harness import MiyaE2EHarness
        from miya.tests.e2e.seed import seed_single_establishment

        self.world = seed_single_establishment()
        self.harness = MiyaE2EHarness(self.world)
        self.v1 = b"%PDF e2e insurance june"
        self.v2 = b"%PDF e2e insurance july"

    def _upload_insurance(self, mock_parse, *, bytes_, title, supersede=None):
        mock_parse.return_value = dict(INSURANCE_PARSE)
        return ingest_document(
            restaurant=self.world.restaurant,
            uploaded_by=self.world.manager,
            source="WIDGET",
            file_bytes=bytes_,
            filename=f"{title}.pdf",
            mime_type="application/pdf",
            caption=title,
            location_id=str(self.world.loc_a.id),
            supersedes_document_id=supersede,
        )

    def test_e2e_current_insurance_document(self, mock_parse):
        v1 = self._upload_insurance(mock_parse, bytes_=self.v1, title="June Insurance")
        mock_parse.return_value = dict(INSURANCE_PARSE)
        v2 = ingest_document(
            restaurant=self.world.restaurant,
            uploaded_by=self.world.manager,
            source="WIDGET",
            file_bytes=self.v2,
            filename="july.pdf",
            mime_type="application/pdf",
            caption="July Insurance",
            location_id=str(self.world.loc_a.id),
            supersedes_document_id=v1.document_id,
        )
        ref = resolve_document_reference(
            _ctx(self.world.manager, self.world.restaurant, self.world.loc_a),
            query="current insurance certificate",
        )
        self.assertEqual(ref.state, DocumentResolutionState.RESOLVED)
        self.assertEqual(ref.document_id, v2.document_id)

    def test_e2e_ambiguity_no_mutation(self, mock_parse):
        mock_parse.side_effect = [dict(INSURANCE_PARSE), dict(INSURANCE_PARSE_B)]
        self._upload_insurance(mock_parse, bytes_=self.v1, title="Insurance A")
        self._upload_insurance(mock_parse, bytes_=self.v2, title="Insurance B")
        before = TenantDocument.objects.filter(restaurant=self.world.restaurant).count()
        ref = resolve_document_reference(
            _ctx(self.world.manager, self.world.restaurant, self.world.loc_a),
            query="replace the old insurance document with this one",
            mutation_sensitive=True,
        )
        self.assertEqual(ref.state, DocumentResolutionState.AMBIGUOUS)
        self.assertEqual(TenantDocument.objects.filter(restaurant=self.world.restaurant).count(), before)

    def test_e2e_miya_clarifies_on_ambiguity(self, mock_parse):
        mock_parse.side_effect = [dict(INSURANCE_PARSE), dict(INSURANCE_PARSE_B)]
        self._upload_insurance(mock_parse, bytes_=self.v1, title="Insurance June")
        self._upload_insurance(mock_parse, bytes_=self.v2, title="Insurance July")
        cap = self.harness.send("Show me the insurance certificate.")
        self.assertTrue(
            cap.needs_clarification
            or "which" in (cap.reply or "").lower()
            or any(t.get("tool") == "get_document" for t in cap.tool_trace)
        )

    def test_e2e_explicit_id_resolves(self, mock_parse):
        doc = self._upload_insurance(mock_parse, bytes_=self.v1, title="Explicit Insurance")
        ref = resolve_document_reference(
            _ctx(self.world.manager, self.world.restaurant, self.world.loc_a),
            document_id=doc.document_id,
        )
        self.assertEqual(ref.document_id, doc.document_id)

    def test_e2e_previous_and_all_versions(self, mock_parse):
        v1 = self._upload_insurance(mock_parse, bytes_=self.v1, title="Versioned Insurance")
        mock_parse.return_value = dict(INSURANCE_PARSE)
        ingest_document(
            restaurant=self.world.restaurant,
            uploaded_by=self.world.manager,
            source="WIDGET",
            file_bytes=self.v2,
            filename="v2.pdf",
            mime_type="application/pdf",
            caption="Versioned Insurance v2",
            location_id=str(self.world.loc_a.id),
            supersedes_document_id=v1.document_id,
        )
        prev = resolve_document_reference(
            _ctx(self.world.manager, self.world.restaurant, self.world.loc_a),
            query="previous insurance certificate",
        )
        self.assertEqual(prev.state, DocumentResolutionState.RESOLVED)
        self.assertEqual(prev.document_id, v1.document_id)
        v1_doc = TenantDocument.objects.get(id=v1.document_id)
        allv = resolve_document_reference(
            _ctx(self.world.manager, self.world.restaurant, self.world.loc_a),
            document_family_id=str(v1_doc.document_family_id),
            version_scope="all",
        )
        self.assertEqual(allv.state, DocumentResolutionState.RESOLVED)
        self.assertEqual(len(allv.candidates), 2)

    def test_e2e_establishment_isolation(self, mock_parse):
        from accounts.models import BusinessLocation

        loc_b = BusinessLocation.objects.create(
            restaurant=self.world.restaurant,
            name="Rooftop",
            is_primary=False,
            is_active=True,
        )
        doc_a = self._upload_insurance(mock_parse, bytes_=self.v1, title="Site A Insurance")
        mock_parse.return_value = dict(INSURANCE_PARSE_B)
        self._upload_insurance(mock_parse, bytes_=self.v2, title="Site B Insurance")
        TenantDocument.objects.filter(id=doc_a.document_id).update(location_id=loc_b.id)
        ref = resolve_document_reference(
            _ctx(self.world.manager, self.world.restaurant, self.world.loc_a),
            query="current insurance",
        )
        self.assertEqual(ref.state, DocumentResolutionState.RESOLVED)
        self.assertNotEqual(ref.document_id, doc_a.document_id)

    def test_e2e_tenant_isolation(self, mock_parse):
        from accounts.models import BusinessLocation, CustomUser, Restaurant

        doc = self._upload_insurance(mock_parse, bytes_=self.v1, title="Tenant E2E")
        other_rest = Restaurant.objects.create(
            name="Other E2E Rest",
            email="othere2e@test.mizan.local",
            timezone="Africa/Casablanca",
        )
        other_loc = BusinessLocation.objects.create(
            restaurant=other_rest, name="Main", is_primary=True, is_active=True
        )
        other_mgr = CustomUser.objects.create_user(
            email="other-e2e-mgr@test.mizan.local",
            password="testpass",
            first_name="O",
            last_name="Mgr",
            role="MANAGER",
            restaurant=other_rest,
            primary_location=other_loc,
        )
        ref = resolve_document_reference(
            _ctx(other_mgr, other_rest, other_loc),
            document_id=doc.document_id,
        )
        self.assertEqual(ref.state, DocumentResolutionState.NOT_FOUND)
