"""Production multimodal extraction provider abstraction (Phase 14.3.4).

Extraction only — no business mutations. Provider output is validated before use.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger("miya.multimodal_extraction")

PROVIDER_OPENAI = "OPENAI"
PROVIDER_FIXTURE = "FIXTURE"

# Legacy parse envelope categories/actions (informational hints — not mutations).
_VALID_CATEGORIES = frozenset(
    {
        "invoice_or_receipt",
        "schedule",
        "process_checklist",
        "id_or_certification",
        "policy_or_handbook",
        "contract",
        "report",
        "equipment_issue",
        "incident",
        "task_or_app_screenshot",
        "inventory",
        "insurance",
        "other",
    }
)
_VALID_SUGGESTED_ACTIONS = frozenset(
    {
        "log_invoice",
        "import_schedule",
        "import_process_templates",
        "open_maintenance_request",
        "report_incident",
        "review_tasks",
        "upload_document",
        "stock_count",
        "ask_manager",
    }
)
_FORBIDDEN_STRUCTURED_KEYS = frozenset(
    {
        "create_invoice",
        "create_incident",
        "create_compliance_record",
        "create_task",
        "create_reminder",
        "create_template",
        "create_meeting",
        "mutate",
        "operation",
    }
)


class ProviderConfigurationError(Exception):
    """Unknown or invalid server-side provider configuration."""


class ExtractionSchemaError(Exception):
    """Provider returned malformed extraction data."""


@dataclass
class MultimodalExtractionRequest:
    media_kind: str  # document | image
    file_bytes: bytes
    content_type: str = ""
    filename: str = ""
    operation_id: str = ""
    correlation_id: str = ""


@dataclass
class MultimodalExtractionResult:
    success: bool
    provider: str
    provider_model: str = ""
    category: str = "other"
    classification: str = ""
    confidence: float = 0.0
    summary: str = ""
    extracted_text: str = ""
    structured_fields: dict[str, Any] = field(default_factory=dict)
    suggested_action: str = "ask_manager"
    error_code: str = ""
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0

    @property
    def extraction_failed(self) -> bool:
        return not self.success or bool(self.error_code)


class MultimodalExtractionProvider(Protocol):
    provider_id: str
    provider_model: str

    def extract(self, request: MultimodalExtractionRequest) -> MultimodalExtractionResult: ...


def get_multimodal_extraction_provider() -> MultimodalExtractionProvider:
    """Server-configured provider selection only — never from user/OCR input."""
    from django.conf import settings

    raw = (getattr(settings, "MULTIMODAL_EXTRACTION_PROVIDER", None) or PROVIDER_OPENAI).strip()
    key = raw.upper()
    if key in (PROVIDER_FIXTURE, "FIXTURE_PROVIDER") or raw.endswith("fixture_extraction_provider"):
        from miya.services.multimodal_providers.fixture_adapter import FixtureExtractionProvider

        return FixtureExtractionProvider()
    if key in (PROVIDER_OPENAI, "", "DEFAULT"):
        from miya.services.multimodal_providers.openai_adapter import OpenAIExtractionProvider

        return OpenAIExtractionProvider()
    raise ProviderConfigurationError(f"Unknown MULTIMODAL_EXTRACTION_PROVIDER: {raw!r}")


def validate_extraction_result(result: MultimodalExtractionResult) -> MultimodalExtractionResult:
    """Reject malformed provider payloads before planning/mutation."""
    if not isinstance(result.structured_fields, dict):
        raise ExtractionSchemaError("structured_fields must be a dict")
    cleaned: dict[str, Any] = {}
    for k, v in result.structured_fields.items():
        key = str(k)
        if key in _FORBIDDEN_STRUCTURED_KEYS:
            result.warnings.append(f"stripped_forbidden_key:{key}")
            continue
        cleaned[key] = v
    result.structured_fields = cleaned
    try:
        result.confidence = max(0.0, min(1.0, float(result.confidence)))
    except (TypeError, ValueError):
        result.confidence = 0.0
        result.success = False
        result.error_code = result.error_code or "extraction_schema_validation_failed"
    cat = (result.category or "other").strip()
    result.category = cat if cat in _VALID_CATEGORIES else "other"
    result.classification = result.category
    action = (result.suggested_action or "ask_manager").strip()
    result.suggested_action = action if action in _VALID_SUGGESTED_ACTIONS else "ask_manager"
    if result.error_code and result.success:
        result.success = False
    return result


def raw_envelope_to_result(
    raw: dict[str, Any],
    *,
    provider: str,
    model: str = "",
) -> MultimodalExtractionResult:
    """Convert legacy parse_document/parse_photo envelope → typed result."""
    err = str(raw.get("error") or "").strip()
    confidence = raw.get("confidence")
    try:
        conf = float(confidence) if confidence is not None else 0.0
    except (TypeError, ValueError):
        conf = 0.0
    fields = raw.get("fields") if isinstance(raw.get("fields"), dict) else {}
    success = not err and conf >= 0.0
    error_code = ""
    if err:
        error_code = err if err.startswith(("unsupported_", "empty_", "provider_", "OPENAI")) else f"extraction_failed:{err[:64]}"
        success = False
    return MultimodalExtractionResult(
        success=success,
        provider=provider,
        provider_model=model,
        category=str(raw.get("category") or "other"),
        classification=str(raw.get("category") or "other"),
        confidence=conf,
        summary=str(raw.get("summary") or "")[:500],
        extracted_text=str(raw.get("extracted_text") or raw.get("raw_response") or "")[:12000],
        structured_fields=dict(fields),
        suggested_action=str(raw.get("suggested_action") or "ask_manager"),
        error_code=error_code,
        metadata={
            "extracted_kind": raw.get("extracted_kind"),
            "extracted_chars": raw.get("extracted_chars"),
            "provider_mode": raw.get("provider_mode"),
        },
    )


def result_to_legacy_envelope(result: MultimodalExtractionResult) -> dict[str, Any]:
    """Bridge typed result → existing tenant_documents parse envelope."""
    envelope: dict[str, Any] = {
        "category": result.category or "other",
        "confidence": result.confidence,
        "summary": result.summary,
        "suggested_action": result.suggested_action,
        "fields": dict(result.structured_fields),
        "provider": result.provider,
        "provider_model": result.provider_model,
        "extraction_success": result.success,
    }
    if result.extracted_text:
        envelope["extracted_text"] = result.extracted_text
    if result.metadata.get("extracted_kind"):
        envelope["extracted_kind"] = result.metadata["extracted_kind"]
    if result.metadata.get("extracted_chars") is not None:
        envelope["extracted_chars"] = result.metadata["extracted_chars"]
    if result.metadata.get("provider_mode"):
        envelope["provider_mode"] = result.metadata["provider_mode"]
    if result.error_code:
        envelope["error"] = result.error_code
        envelope["extraction_failed"] = True
    if result.warnings:
        envelope["extraction_warnings"] = list(result.warnings)
    if result.duration_ms:
        envelope["extraction_duration_ms"] = round(result.duration_ms, 2)
    if result.metadata.get("operation_id"):
        envelope["operation_id"] = result.metadata["operation_id"]
    return envelope


def _record_extraction_observability(
    *,
    request: MultimodalExtractionRequest,
    result: MultimodalExtractionResult,
) -> None:
    """Structured log — no raw bytes or full OCR text."""
    try:
        logger.info(
            "MIYA_EXTRACTION_TRACE provider=%s model=%s media=%s success=%s error=%s "
            "category=%s duration_ms=%.1f operation_id=%s filename=%s",
            result.provider,
            result.provider_model or "",
            request.media_kind,
            result.success,
            result.error_code or "",
            result.category,
            result.duration_ms,
            (request.operation_id or "")[:64],
            (request.filename or "")[:120],
        )
    except Exception:
        pass


def run_extraction(request: MultimodalExtractionRequest) -> dict[str, Any]:
    """Canonical extraction entry — select provider, validate, return legacy envelope."""
    if not request.file_bytes:
        result = MultimodalExtractionResult(
            success=False,
            provider="none",
            error_code="empty_file",
            summary="Empty file.",
        )
        return result_to_legacy_envelope(result)

    started = time.perf_counter()
    try:
        provider = get_multimodal_extraction_provider()
    except ProviderConfigurationError as exc:
        result = MultimodalExtractionResult(
            success=False,
            provider="none",
            error_code="provider_configuration_error",
            summary=str(exc),
        )
        return result_to_legacy_envelope(result)

    try:
        result = provider.extract(request)
    except Exception as exc:
        result = MultimodalExtractionResult(
            success=False,
            provider=getattr(provider, "provider_id", "unknown"),
            provider_model=getattr(provider, "provider_model", ""),
            error_code="provider_unavailable",
            summary="Extraction provider failed.",
            metadata={"exception_type": type(exc).__name__},
        )
    result.duration_ms = (time.perf_counter() - started) * 1000
    if request.operation_id:
        result.metadata["operation_id"] = request.operation_id
    provider_id = result.provider
    try:
        result = validate_extraction_result(result)
    except ExtractionSchemaError as exc:
        result = MultimodalExtractionResult(
            success=False,
            provider=provider_id,
            error_code="extraction_schema_validation_failed",
            summary=str(exc),
            duration_ms=(time.perf_counter() - started) * 1000,
        )
    _record_extraction_observability(request=request, result=result)
    return result_to_legacy_envelope(result)


def run_document_extraction(
    blob: bytes,
    *,
    content_type: str = "",
    filename: str = "",
    operation_id: str = "",
) -> dict[str, Any]:
    return run_extraction(
        MultimodalExtractionRequest(
            media_kind="document",
            file_bytes=blob,
            content_type=content_type,
            filename=filename,
            operation_id=operation_id,
        )
    )


def run_photo_extraction(
    image_bytes: bytes,
    *,
    content_type: str = "image/jpeg",
    operation_id: str = "",
) -> dict[str, Any]:
    return run_extraction(
        MultimodalExtractionRequest(
            media_kind="image",
            file_bytes=image_bytes,
            content_type=content_type,
            operation_id=operation_id,
        )
    )


# Backward-compatible hooks (deprecated — use run_*_extraction)
def maybe_parse_document(blob: bytes, *, content_type: str = "", name: str = "") -> dict[str, Any] | None:
    from django.conf import settings

    configured = (getattr(settings, "MULTIMODAL_EXTRACTION_PROVIDER", None) or "").strip()
    if not configured:
        return None
    return run_document_extraction(blob, content_type=content_type, filename=name)


def maybe_parse_photo(image_bytes: bytes, *, content_type: str = "image/jpeg") -> dict[str, Any] | None:
    from django.conf import settings

    configured = (getattr(settings, "MULTIMODAL_EXTRACTION_PROVIDER", None) or "").strip()
    if not configured:
        return None
    return run_photo_extraction(image_bytes, content_type=content_type)
