"""Optional real OpenAI extraction provider integration — NOT part of deterministic CI."""
from __future__ import annotations

import os
import unittest

from django.test import TestCase, override_settings

from finance.models import Invoice
from miya.services.multimodal_extraction_provider import run_document_extraction
from miya.services.document_input import ingest_document


def _openai_configured() -> bool:
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    explicit = (os.environ.get("RUN_OPENAI_EXTRACTION_INTEGRATION") or "").lower() in (
        "1",
        "true",
        "yes",
    )
    return bool(key and explicit)


@unittest.skipUnless(
    _openai_configured(),
    "Set OPENAI_API_KEY and RUN_OPENAI_EXTRACTION_INTEGRATION=1 to run real provider test",
)
class RealOpenAIExtractionIntegrationTests(TestCase):
    """Separated from fixture PostgreSQL E2E — requires live OpenAI credentials."""

    @override_settings(MULTIMODAL_EXTRACTION_PROVIDER="OPENAI")
    def test_real_openai_provider_returns_valid_envelope_no_mutation(self):
        from accounts.models import BusinessLocation, CustomUser, Restaurant

        rest = Restaurant.objects.create(
            name="OpenAI Integ Rest",
            email="openai-integ@test.mizan.local",
            timezone="Africa/Casablanca",
        )
        loc = BusinessLocation.objects.create(
            restaurant=rest,
            name="Main",
            is_primary=True,
            is_active=True,
        )
        user = CustomUser.objects.create_user(
            email="openai-integ-user@test.mizan.local",
            password="testpass",
            role="MANAGER",
            restaurant=rest,
            primary_location=loc,
        )
        pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
        inv_before = Invoice.objects.filter(restaurant=rest).count()
        envelope = run_document_extraction(pdf, content_type="application/pdf", filename="blank.pdf")
        self.assertIn("category", envelope)
        self.assertIn("provider", envelope)
        self.assertEqual(envelope.get("provider"), "OPENAI")
        doc_input = ingest_document(
            restaurant=rest,
            uploaded_by=user,
            source="WIDGET",
            file_bytes=pdf,
            filename="blank.pdf",
            mime_type="application/pdf",
            location_id=str(loc.id),
        )
        self.assertTrue(doc_input.document_id)
        self.assertEqual(Invoice.objects.filter(restaurant=rest).count(), inv_before)
