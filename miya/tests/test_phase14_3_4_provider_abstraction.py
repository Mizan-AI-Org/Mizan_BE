"""Phase 14.3.4 — production multimodal provider abstraction tests."""
from __future__ import annotations

import logging
from io import StringIO
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from accounts.models import BusinessLocation, CustomUser, Restaurant
from finance.models import Invoice
from miya.models import TenantDocument
from miya.services.document_input import ingest_document
from miya.services.multimodal_extraction_provider import (
    ExtractionSchemaError,
    MultimodalExtractionRequest,
    MultimodalExtractionResult,
    ProviderConfigurationError,
    get_multimodal_extraction_provider,
    raw_envelope_to_result,
    result_to_legacy_envelope,
    run_document_extraction,
    run_extraction,
    validate_extraction_result,
)
from miya.services.multimodal_providers.fixture_adapter import FixtureExtractionProvider
from miya.services.multimodal_providers.openai_adapter import OpenAIExtractionProvider
from miya.tests.e2e.fixtures.multimodal_bytes import (
    insurance_v1_pdf,
    provider_error_pdf,
)
from payroll.models import ComplianceDocument
from staff.models_task import SafetyConcernReport
from scheduling.task_templates import TaskTemplate


def _seed_world():
    rest = Restaurant.objects.create(
        name="Phase1434 Rest",
        email="p1434@test.mizan.local",
        timezone="Africa/Casablanca",
    )
    loc = BusinessLocation.objects.create(
        restaurant=rest,
        name="Main",
        is_primary=True,
        is_active=True,
    )
    other = BusinessLocation.objects.create(
        restaurant=rest,
        name="Other Site",
        is_primary=False,
        is_active=True,
    )
    other_rest = Restaurant.objects.create(
        name="Other Tenant",
        email="other1434@test.mizan.local",
        timezone="Africa/Casablanca",
    )
    manager = CustomUser.objects.create_user(
        email="mgr-1434@test.mizan.local",
        password="testpass",
        first_name="Mgr",
        last_name="Test",
        role="MANAGER",
        restaurant=rest,
        primary_location=loc,
    )
    manager.managed_locations.add(loc)
    return rest, loc, other, other_rest, manager


class ProviderContractTests(TestCase):
    @override_settings(MULTIMODAL_EXTRACTION_PROVIDER="FIXTURE")
    def test_fixture_provider_implements_contract(self):
        provider = get_multimodal_extraction_provider()
        self.assertIsInstance(provider, FixtureExtractionProvider)
        req = MultimodalExtractionRequest(
            media_kind="document",
            file_bytes=insurance_v1_pdf(),
            content_type="application/pdf",
            filename="insurance.pdf",
        )
        result = provider.extract(req)
        self.assertTrue(result.success)
        self.assertEqual(result.provider, "FIXTURE")
        validated = validate_extraction_result(result)
        envelope = result_to_legacy_envelope(validated)
        self.assertEqual(envelope["category"], "id_or_certification")
        self.assertIn("fields", envelope)
        self.assertNotIn("create_invoice", envelope["fields"])

    @override_settings(MULTIMODAL_EXTRACTION_PROVIDER="OPENAI")
    @patch("scheduling.document_router_service._openai_parse_document_impl")
    def test_openai_provider_implements_contract(self, mock_impl):
        mock_impl.return_value = {
            "category": "invoice_or_receipt",
            "confidence": 0.9,
            "summary": "Invoice",
            "suggested_action": "log_invoice",
            "fields": {"vendor": "Acme", "amount": 100},
        }
        provider = get_multimodal_extraction_provider()
        self.assertIsInstance(provider, OpenAIExtractionProvider)
        req = MultimodalExtractionRequest(
            media_kind="document",
            file_bytes=b"%PDF fake",
            content_type="application/pdf",
        )
        result = provider.extract(req)
        self.assertTrue(result.success)
        self.assertEqual(result.provider, "OPENAI")
        self.assertEqual(result.provider_model, "gpt-4o")


class ProviderSelectionTests(TestCase):
    @override_settings(MULTIMODAL_EXTRACTION_PROVIDER="UNKNOWN_XYZ")
    def test_unknown_provider_fails_closed(self):
        with self.assertRaises(ProviderConfigurationError):
            get_multimodal_extraction_provider()

    @override_settings(MULTIMODAL_EXTRACTION_PROVIDER="UNKNOWN_XYZ")
    def test_unknown_provider_run_extraction_returns_configuration_error(self):
        out = run_document_extraction(b"data")
        self.assertFalse(out.get("extraction_success", True))
        self.assertEqual(out.get("error"), "provider_configuration_error")

    @override_settings(MULTIMODAL_EXTRACTION_PROVIDER="")
    def test_missing_provider_defaults_to_openai(self):
        provider = get_multimodal_extraction_provider()
        self.assertIsInstance(provider, OpenAIExtractionProvider)

    @override_settings(MULTIMODAL_EXTRACTION_PROVIDER="miya.tests.e2e.fixture_extraction_provider")
    def test_legacy_fixture_module_path_still_selects_fixture(self):
        provider = get_multimodal_extraction_provider()
        self.assertIsInstance(provider, FixtureExtractionProvider)


class SchemaValidationTests(TestCase):
    def test_valid_response_passes(self):
        result = MultimodalExtractionResult(
            success=True,
            provider="TEST",
            category="other",
            confidence=0.5,
            structured_fields={"vendor": "x"},
        )
        out = validate_extraction_result(result)
        self.assertTrue(out.success)

    def test_malformed_structured_fields_rejected(self):
        result = MultimodalExtractionResult(
            success=True,
            provider="TEST",
            structured_fields="not-a-dict",  # type: ignore[arg-type]
        )
        with self.assertRaises(ExtractionSchemaError):
            validate_extraction_result(result)

    def test_wrong_confidence_type_marks_failure(self):
        result = MultimodalExtractionResult(
            success=True,
            provider="TEST",
            confidence="high",  # type: ignore[arg-type]
        )
        out = validate_extraction_result(result)
        self.assertFalse(out.success)
        self.assertEqual(out.error_code, "extraction_schema_validation_failed")

    def test_forbidden_mutation_keys_stripped(self):
        result = MultimodalExtractionResult(
            success=True,
            provider="TEST",
            structured_fields={
                "vendor": "Acme",
                "create_invoice": {"amount": 999},
                "create_incident": True,
            },
        )
        out = validate_extraction_result(result)
        self.assertNotIn("create_invoice", out.structured_fields)
        self.assertNotIn("create_incident", out.structured_fields)
        self.assertEqual(out.structured_fields.get("vendor"), "Acme")

    @override_settings(MULTIMODAL_EXTRACTION_PROVIDER="FIXTURE")
    def test_malformed_provider_payload_rejected_at_boundary(self):
        class BadProvider:
            provider_id = "BAD"
            provider_model = "bad"

            def extract(self, request):
                return MultimodalExtractionResult(
                    success=True,
                    provider="BAD",
                    structured_fields="bad",  # type: ignore[arg-type]
                )

        with patch(
            "miya.services.multimodal_extraction_provider.get_multimodal_extraction_provider",
            return_value=BadProvider(),
        ):
            out = run_extraction(
                MultimodalExtractionRequest(media_kind="document", file_bytes=b"x")
            )
        self.assertTrue(out.get("extraction_failed"))
        self.assertEqual(out.get("error"), "extraction_schema_validation_failed")


class ProviderFailureTests(TestCase):
    @override_settings(MULTIMODAL_EXTRACTION_PROVIDER="FIXTURE")
    def test_provider_timeout_structured_failure(self):
        out = run_document_extraction(provider_error_pdf())
        self.assertTrue(out.get("extraction_failed") or out.get("error"))
        self.assertEqual(out.get("error"), "provider_timeout")
        self.assertFalse(out.get("extraction_success", True))

    def test_empty_file_structured_failure(self):
        out = run_document_extraction(b"")
        self.assertEqual(out.get("error"), "empty_file")

    @override_settings(MULTIMODAL_EXTRACTION_PROVIDER="FIXTURE")
    @patch("miya.services.multimodal_providers.fixture_adapter.FixtureExtractionProvider.extract")
    def test_provider_exception_structured_failure(self, mock_extract):
        mock_extract.side_effect = TimeoutError("timed out")
        out = run_document_extraction(insurance_v1_pdf())
        self.assertEqual(out.get("error"), "provider_unavailable")


class ProviderCannotMutateTests(TestCase):
    _FORBIDDEN = (
        "create_invoice",
        "create_incident",
        "create_compliance_record",
        "create_task",
        "create_reminder",
        "create_template",
    )

    def test_provider_response_has_no_mutation_commands(self):
        for key in self._FORBIDDEN:
            raw = {
                "category": "other",
                "confidence": 0.9,
                "summary": "x",
                "fields": {key: {"foo": "bar"}},
            }
            result = raw_envelope_to_result(raw, provider="MOCK")
            validated = validate_extraction_result(result)
            self.assertNotIn(key, validated.structured_fields)

    @override_settings(MULTIMODAL_EXTRACTION_PROVIDER="FIXTURE")
    def test_fixture_extract_never_returns_mutation_keys(self):
        provider = FixtureExtractionProvider()
        req = MultimodalExtractionRequest(
            media_kind="document",
            file_bytes=insurance_v1_pdf(),
        )
        result = validate_extraction_result(provider.extract(req))
        for key in self._FORBIDDEN:
            self.assertNotIn(key, result.structured_fields)


@patch("miya.services.tenant_documents._parse_upload")
class UploadDoesNotMutateTests(TestCase):
    def setUp(self):
        self.rest, self.loc, self.other, self.other_rest, self.manager = _seed_world()

    def test_upload_does_not_create_invoice(self, mock_parse):
        mock_parse.return_value = {
            "category": "invoice_or_receipt",
            "confidence": 0.95,
            "summary": "Invoice",
            "suggested_action": "log_invoice",
            "fields": {"vendor": "Acme", "amount": 1500},
            "provider": "FIXTURE",
        }
        before = Invoice.objects.filter(restaurant=self.rest).count()
        ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=b"%PDF invoice",
            filename="inv.pdf",
            mime_type="application/pdf",
            location_id=str(self.loc.id),
        )
        self.assertEqual(Invoice.objects.filter(restaurant=self.rest).count(), before)

    def test_upload_does_not_create_incident(self, mock_parse):
        mock_parse.return_value = {
            "category": "incident",
            "confidence": 0.9,
            "summary": "Incident",
            "suggested_action": "report_incident",
            "fields": {"severity": "high"},
        }
        before = SafetyConcernReport.objects.filter(restaurant=self.rest).count()
        ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=b"\xff\xd8\xff",
            filename="inc.jpg",
            mime_type="image/jpeg",
        )
        self.assertEqual(SafetyConcernReport.objects.filter(restaurant=self.rest).count(), before)

    def test_upload_does_not_create_compliance_or_task_template(self, mock_parse):
        mock_parse.return_value = {
            "category": "id_or_certification",
            "confidence": 0.9,
            "summary": "Cert",
            "fields": {"document_type": "hygiene"},
        }
        c_before = ComplianceDocument.objects.filter(restaurant=self.rest).count()
        t_before = TaskTemplate.objects.filter(restaurant=self.rest).count()
        ingest_document(
            restaurant=self.rest,
            uploaded_by=self.manager,
            source="WIDGET",
            file_bytes=b"%PDF cert",
            filename="cert.pdf",
            mime_type="application/pdf",
        )
        self.assertEqual(ComplianceDocument.objects.filter(restaurant=self.rest).count(), c_before)
        self.assertEqual(TaskTemplate.objects.filter(restaurant=self.rest).count(), t_before)


class TenantEstablishmentIsolationTests(TestCase):
    @override_settings(MULTIMODAL_EXTRACTION_PROVIDER="FIXTURE")
    def test_provider_request_has_no_tenant_from_untrusted_metadata(self):
        """Provider contract must not accept tenant/establishment from OCR/filename."""
        fields = MultimodalExtractionRequest.__dataclass_fields__
        self.assertNotIn("tenant_id", fields)
        self.assertNotIn("restaurant_id", fields)
        self.assertNotIn("establishment_id", fields)

    @patch("miya.services.tenant_documents._parse_upload")
    def test_ingest_uses_trusted_restaurant_not_ocr_hint(self, mock_parse):
        rest, loc, other, other_rest, manager = _seed_world()
        mock_parse.return_value = {
            "category": "other",
            "confidence": 0.5,
            "summary": "doc",
            "fields": {"establishment": "Evil Other Tenant", "restaurant_id": str(other_rest.id)},
        }
        doc_input = ingest_document(
            restaurant=rest,
            uploaded_by=manager,
            source="WIDGET",
            file_bytes=b"%PDF",
            filename=f"evil-{other_rest.id}.pdf",
            mime_type="application/pdf",
            location_id=str(loc.id),
        )
        doc = TenantDocument.objects.get(id=doc_input.document_id)
        self.assertEqual(str(doc.restaurant_id), str(rest.id))
        self.assertEqual(str(doc.location_id), str(loc.id))


class ObservabilityTests(TestCase):
    @override_settings(MULTIMODAL_EXTRACTION_PROVIDER="FIXTURE")
    def test_provider_metadata_in_envelope_and_log(self):
        log_capture = StringIO()
        handler = logging.StreamHandler(log_capture)
        handler.setLevel(logging.INFO)
        logger = logging.getLogger("miya.multimodal_extraction")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            out = run_document_extraction(
                insurance_v1_pdf(),
                filename="insurance.pdf",
                operation_id="op-1434-obs",
            )
        finally:
            logger.removeHandler(handler)
        self.assertEqual(out.get("provider"), "FIXTURE")
        self.assertEqual(out.get("provider_model"), "fixture-v1")
        self.assertIn("extraction_duration_ms", out)
        log_text = log_capture.getvalue()
        self.assertIn("MIYA_EXTRACTION_TRACE", log_text)
        self.assertIn("provider=FIXTURE", log_text)
        self.assertIn("operation_id=op-1434-obs", log_text)


class ExtractionFailurePlanningBoundaryTests(TestCase):
    @patch("miya.services.tenant_documents._parse_upload")
    def test_failed_extraction_marks_document_failed_no_business_mutation(self, mock_parse):
        rest, loc, other, other_rest, manager = _seed_world()
        mock_parse.return_value = {
            "category": "other",
            "confidence": 0.0,
            "summary": "Failed",
            "error": "provider_timeout",
            "extraction_failed": True,
            "fields": {},
        }
        inv_before = Invoice.objects.filter(restaurant=rest).count()
        doc_input = ingest_document(
            restaurant=rest,
            uploaded_by=manager,
            source="WIDGET",
            file_bytes=b"%PDF",
            filename="bad.pdf",
            mime_type="application/pdf",
        )
        doc = TenantDocument.objects.get(id=doc_input.document_id)
        self.assertEqual(doc.processing_status, "failed")
        self.assertEqual(Invoice.objects.filter(restaurant=rest).count(), inv_before)
        meta = doc.parse_metadata or {}
        self.assertTrue(meta.get("error") or doc.processing_status == "failed")
