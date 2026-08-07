"""FIXTURE provider adapter — Phase 14.3.3 deterministic boundary."""
from __future__ import annotations

from miya.services.multimodal_extraction_provider import (
    MultimodalExtractionRequest,
    MultimodalExtractionResult,
    raw_envelope_to_result,
)


class FixtureExtractionProvider:
    provider_id = "FIXTURE"
    provider_model = "fixture-v1"

    def extract(self, request: MultimodalExtractionRequest) -> MultimodalExtractionResult:
        from miya.tests.e2e import fixture_extraction_provider as fixture_mod

        if request.media_kind == "image":
            raw = fixture_mod.parse_photo(
                request.file_bytes,
                content_type=request.content_type or "image/jpeg",
            )
        else:
            raw = fixture_mod.parse_document(
                request.file_bytes,
                content_type=request.content_type,
                name=request.filename,
            )
        result = raw_envelope_to_result(raw, provider=self.provider_id, model=self.provider_model)
        result.metadata["provider_mode"] = getattr(fixture_mod, "PROVIDER_MODE", "FIXTURE_PROVIDER")
        result.metadata["media_kind"] = request.media_kind
        return result
