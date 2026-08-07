"""OpenAI GPT-4o extraction adapter — wraps legacy parse implementations."""
from __future__ import annotations

from miya.services.multimodal_extraction_provider import (
    MultimodalExtractionRequest,
    MultimodalExtractionResult,
    raw_envelope_to_result,
)


class OpenAIExtractionProvider:
    provider_id = "OPENAI"
    provider_model = "gpt-4o"

    def extract(self, request: MultimodalExtractionRequest) -> MultimodalExtractionResult:
        if request.media_kind == "image":
            from scheduling.photo_router_service import _openai_parse_photo_impl

            raw = _openai_parse_photo_impl(
                request.file_bytes,
                content_type=request.content_type or "image/jpeg",
            )
        else:
            from scheduling.document_router_service import _openai_parse_document_impl

            raw = _openai_parse_document_impl(
                request.file_bytes,
                content_type=request.content_type,
                name=request.filename,
            )
        result = raw_envelope_to_result(raw, provider=self.provider_id, model=self.provider_model)
        result.metadata.setdefault("media_kind", request.media_kind)
        return result
