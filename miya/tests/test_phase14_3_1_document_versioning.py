"""Phase 14.3.1 — TenantDocument versioning (Wave 1)."""
from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from accounts.models import BusinessLocation, CustomUser, Restaurant
from miya.models import TenantDocument
from miya.services.document_input import ingest_document
from miya.services.document_versioning import (
    compute_content_hash,
    get_document_versions,
    resolve_current_version,
)
from miya.services.ops.context import OpsContext
from miya.services.ops.documents import find_documents, get_document
from miya.tests.e2e.harness import PostgresE2ETestCase

INSURANCE_PARSE = {
    "category": "insurance",
    "confidence": 0.91,
    "summary": "Insurance policy expiring 30 September 2026",
    "fields": {"expiry_date": "2026-09-30", "document_type": "insurance"},
}

INSURANCE_PARSE_V2 = {
    "category": "insurance",
    "confidence": 0.93,
    "summary": "Renewed insurance policy expiring 30 September 2027",
    "fields": {"expiry_date": "2027-09-30", "document_type": "insurance"},
}


def _seed():
    rest = Restaurant.objects.create(
        name="Ver Rest",
        email="ver@test.mizan.local",
        timezone="Africa/Casablanca",
    )
    other = Restaurant.objects.create(
        name="Other Ver Rest",
        email="over@test.mizan.local",
        timezone="Africa/Casablanca",
    )
    loc = BusinessLocation.objects.create(
        restaurant=rest, name="Main", is_primary=True, is_active=True
    )
    mgr = CustomUser.objects.create_user(
        email="ver-mgr@test.mizan.local",
        password="testpass",
        first_name="Mgr",
        last_name="Ver",
        role="MANAGER",
        restaurant=rest,
        primary_location=loc,
    )
    mgr.managed_locations.add(loc)
    return rest, other, loc, mgr


@patch("miya.services.tenant_documents._parse_upload")
class DocumentVersioningTests(TestCase):
    def setUp(self):
        self.rest, self.other, self.loc, self.manager = _seed()
        self.v1_bytes = b"%PDF-1.4 insurance policy v1 content"
        self.v2_bytes = b"%PDF-1.4 insurance policy v2 renewed content"

    def test_first_upload_is_version_one(self, mock_parse):
        mock_parse.return_value = dict(INSURANCE_PARSE)
        doc_input = ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.v1_bytes,
            filename="insurance.pdf",
            mime_type="application/pdf",
            location_id=str(self.loc.id),
        )
        doc = TenantDocument.objects.get(id=doc_input.document_id)
        self.assertEqual(doc.version_number, 1)
        self.assertTrue(doc.is_current)
        self.assertEqual(doc.content_hash, compute_content_hash(self.v1_bytes))
        self.assertEqual(str(doc.document_family_id), str(doc.id))
        self.assertIsNone(doc.supersedes_id)
        self.assertEqual(doc_input.version_number, 1)
        self.assertTrue(doc_input.is_current)

    def test_same_content_retry_is_idempotent(self, mock_parse):
        mock_parse.return_value = dict(INSURANCE_PARSE)
        first = ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.v1_bytes,
            filename="insurance.pdf",
            mime_type="application/pdf",
            operation_id="op-retry-a",
        )
        count = TenantDocument.objects.filter(restaurant=self.rest).count()
        second = ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.v1_bytes,
            filename="insurance-renamed.pdf",
            mime_type="application/pdf",
            operation_id="op-retry-b",
        )
        self.assertEqual(TenantDocument.objects.filter(restaurant=self.rest).count(), count)
        self.assertEqual(first.document_id, second.document_id)
        self.assertTrue(second.is_duplicate)

    def test_same_operation_id_retry_is_idempotent(self, mock_parse):
        mock_parse.return_value = dict(INSURANCE_PARSE)
        op = "op-idem-1431"
        first = ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.v1_bytes,
            filename="insurance.pdf",
            mime_type="application/pdf",
            operation_id=op,
        )
        second = ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.v1_bytes,
            filename="insurance.pdf",
            mime_type="application/pdf",
            operation_id=op,
        )
        self.assertEqual(first.document_id, second.document_id)
        self.assertTrue(second.is_duplicate)

    def test_changed_document_creates_version_two(self, mock_parse):
        mock_parse.side_effect = [dict(INSURANCE_PARSE), dict(INSURANCE_PARSE_V2)]
        v1 = ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.v1_bytes,
            filename="insurance-v1.pdf",
            mime_type="application/pdf",
        )
        v1_doc = TenantDocument.objects.get(id=v1.document_id)
        v2 = ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.v2_bytes,
            filename="insurance-v2.pdf",
            mime_type="application/pdf",
            supersedes_document_id=v1.document_id,
        )
        v1_doc.refresh_from_db()
        v2_doc = TenantDocument.objects.get(id=v2.document_id)
        self.assertFalse(v1_doc.is_current)
        self.assertTrue(v2_doc.is_current)
        self.assertEqual(v2_doc.version_number, 2)
        self.assertEqual(str(v2_doc.supersedes_id), v1.document_id)
        self.assertEqual(v1_doc.document_family_id, v2_doc.document_family_id)
        self.assertNotEqual(v1_doc.file.name, v2_doc.file.name)

    def test_current_version_resolution(self, mock_parse):
        mock_parse.side_effect = [dict(INSURANCE_PARSE), dict(INSURANCE_PARSE_V2)]
        v1 = ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.v1_bytes,
            filename="insurance-v1.pdf",
            mime_type="application/pdf",
        )
        v2 = ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.v2_bytes,
            filename="insurance-v2.pdf",
            mime_type="application/pdf",
            supersedes_document_id=v1.document_id,
        )
        v1_doc = TenantDocument.objects.get(id=v1.document_id)
        current = resolve_current_version(v1_doc)
        self.assertEqual(str(current.id), v2.document_id)

    def test_historical_version_retrieval(self, mock_parse):
        mock_parse.side_effect = [dict(INSURANCE_PARSE), dict(INSURANCE_PARSE_V2)]
        v1 = ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.v1_bytes,
            filename="insurance-v1.pdf",
            mime_type="application/pdf",
        )
        ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.v2_bytes,
            filename="insurance-v2.pdf",
            mime_type="application/pdf",
            supersedes_document_id=v1.document_id,
        )
        v1_doc = TenantDocument.objects.get(id=v1.document_id)
        versions = get_document_versions(str(self.rest.id), str(v1_doc.document_family_id))
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0].version_number, 1)
        self.assertEqual(versions[1].version_number, 2)
        self.assertFalse(versions[0].is_current)
        self.assertTrue(versions[1].is_current)

    def test_find_documents_returns_current_only(self, mock_parse):
        mock_parse.side_effect = [dict(INSURANCE_PARSE), dict(INSURANCE_PARSE_V2)]
        v1 = ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.v1_bytes,
            filename="insurance-v1.pdf",
            mime_type="application/pdf",
        )
        v2 = ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.v2_bytes,
            filename="insurance-v2.pdf",
            mime_type="application/pdf",
            supersedes_document_id=v1.document_id,
        )
        ctx = OpsContext.from_session(
            user=self.manager,
            restaurant=self.rest,
            session_context={
                "restaurant_id": str(self.rest.id),
                "user_id": str(self.manager.id),
                "location_id": str(self.loc.id),
                "channel": "dashboard",
            },
        )
        found = find_documents(ctx, q="insurance", limit=10)
        ids = {d["id"] for d in (found.data or {}).get("documents") or []}
        self.assertIn(v2.document_id, ids)
        self.assertNotIn(v1.document_id, ids)

    def test_get_document_includes_version_history(self, mock_parse):
        mock_parse.side_effect = [dict(INSURANCE_PARSE), dict(INSURANCE_PARSE_V2)]
        v1 = ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.v1_bytes,
            filename="insurance-v1.pdf",
            mime_type="application/pdf",
        )
        ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.v2_bytes,
            filename="insurance-v2.pdf",
            mime_type="application/pdf",
            supersedes_document_id=v1.document_id,
        )
        ctx = OpsContext.from_session(
            user=self.manager,
            restaurant=self.rest,
            session_context={"restaurant_id": str(self.rest.id), "location_id": str(self.loc.id)},
        )
        detail = get_document(ctx, document_id=v1.document_id)
        history = (detail.data or {}).get("document", {}).get("version_history") or []
        self.assertEqual(len(history), 2)
        self.assertTrue((detail.data or {}).get("document", {}).get("current_version_id"))

    def test_tenant_isolation_for_content_hash(self, mock_parse):
        mock_parse.return_value = dict(INSURANCE_PARSE)
        ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=self.v1_bytes,
            filename="insurance.pdf",
            mime_type="application/pdf",
        )
        other_mgr = CustomUser.objects.create_user(
            email="over-mgr@test.mizan.local",
            password="testpass",
            first_name="O",
            last_name="Mgr",
            role="MANAGER",
            restaurant=self.other,
        )
        other_loc = BusinessLocation.objects.create(
            restaurant=self.other, name="Main", is_primary=True, is_active=True
        )
        other_mgr.managed_locations.add(other_loc)
        other_input = ingest_document(
            restaurant=self.other,
            uploaded_by=other_mgr,
            source="WIDGET",
            file_bytes=self.v1_bytes,
            filename="insurance.pdf",
            mime_type="application/pdf",
        )
        self.assertNotEqual(
            TenantDocument.objects.filter(restaurant=self.rest).count(),
            0,
        )
        self.assertEqual(TenantDocument.objects.filter(restaurant=self.other).count(), 1)
        self.assertTrue(other_input.document_id)


class DocumentVersioningPostgresE2ETests(PostgresE2ETestCase):
    @patch("miya.services.tenant_documents._parse_upload")
    def test_postgres_version_chain_persists(self, mock_parse):
        from miya.tests.e2e.seed import seed_single_establishment

        mock_parse.side_effect = [
            dict(INSURANCE_PARSE),
            dict(INSURANCE_PARSE_V2),
        ]
        world = seed_single_establishment()
        v1_bytes = b"%PDF postgres insurance v1"
        v2_bytes = b"%PDF postgres insurance v2"
        v1 = ingest_document(
            restaurant=world.restaurant,
            uploaded_by=world.manager,
            source="WIDGET",
            file_bytes=v1_bytes,
            filename="pg-insurance-v1.pdf",
            mime_type="application/pdf",
            location_id=str(world.location.id),
        )
        v2 = ingest_document(
            restaurant=world.restaurant,
            uploaded_by=world.manager,
            source="WIDGET",
            file_bytes=v2_bytes,
            filename="pg-insurance-v2.pdf",
            mime_type="application/pdf",
            supersedes_document_id=v1.document_id,
        )
        v1_doc = TenantDocument.objects.get(id=v1.document_id)
        v2_doc = TenantDocument.objects.get(id=v2.document_id)
        self.assertEqual(v1_doc.version_number, 1)
        self.assertEqual(v2_doc.version_number, 2)
        self.assertFalse(v1_doc.is_current)
        self.assertTrue(v2_doc.is_current)
        self.assertEqual(v1_doc.document_family_id, v2_doc.document_family_id)
        self.assertTrue(v1_doc.file.name)
        self.assertTrue(v2_doc.file.name)
