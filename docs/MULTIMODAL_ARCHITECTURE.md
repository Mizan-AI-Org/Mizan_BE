# Multimodal Architecture — Mizan / Miya

**Status:** Phase 14.3.4 COMPLETE (production provider abstraction)  
**Related:** [`PHASE_14_3_4_REPORT.md`](../PHASE_14_3_4_REPORT.md), [`PHASE_14_3_3_REPORT.md`](../PHASE_14_3_3_REPORT.md), [`PHASE_14_2_REPORT.md`](../PHASE_14_2_REPORT.md), [`PHASE_14_ARCHITECTURE_AUDIT.md`](../PHASE_14_ARCHITECTURE_AUDIT.md), [`LEGACY_FAST_PATH_AUDIT.md`](LEGACY_FAST_PATH_AUDIT.md), [`TEST_DATABASE.md`](TEST_DATABASE.md)

---

## Principle

**OCR is not intelligence.** Extraction answers *what text/fields are visible*. Miya answers *what this means for the restaurant* and *what should happen next* — always through the canonical execution spine with DB verification.

```
Extraction (B)  →  Evidence stored on TenantDocument
Reasoning (C)   →  Miya UNDERSTAND + PLAN over structured fields + current DB
Action (C)      →  authorize → execute → verify → audit → notify
```

Never represent inference as fact. Never let OCR directly mutate operational state.

---

## Layer model

| Layer | Owner | Source of truth | Example |
|-------|-------|-----------------|---------|
| **L0 — Raw media** | Mizan storage | S3 file bytes | `TenantDocument.file` |
| **L1 — Extraction** | Parse services | `structured_fields`, `parse_metadata`, `extracted_text` | `expiry_date: 2026-12-01`, `confidence: 0.82` |
| **L2 — Document entity** | Mizan DB | `TenantDocument` row + links | `invoice_id`, `compliance_document_id` |
| **L3 — Operational state** | Mizan domain | Tasks, incidents, invoices, reminders | Invoice status = APPROVED |
| **L4 — Audit history** | OperationalEvent | Append-only timeline | "Approved by Manager X" |
| **L5 — Conversation** | Session/working set | Ephemeral | "this document", attachment_ids |

**Resolution order for answers:** L3 > L4 > L1 > L5

---

## Pipeline stages

### 1. Ingestion (A — Phase 14.2)

**Canonical entry:** `miya/services/document_input.py` → `ingest_document()`

**Channel entry points (all converge on DocumentInput):**
- Dashboard: `miya/views.py` → `miya_upload_attachment()` → `ingest_document(source=WIDGET)`
- WhatsApp: `miya/services/whatsapp_attachments.py` → `ingest_document(source=WHATSAPP)`
- Agent tools: `parse_photo` / `parse_document` (extraction-only by default; `auto_create=false`)

**Responsibilities:** validate tenant/establishment/actor, idempotent store, extract, normalize, build multimodal context, return `DocumentInput`.

**Must NOT:** create Invoice, Incident, ComplianceDocument, or send mutation notifications. Promotion requires explicit `promote_linked_records=True` (legacy opt-in only).

### 2. Extraction (B — Phase 14.3.4)

**Canonical boundary:** `miya/services/multimodal_extraction_provider.py`

```
parse_document() / parse_photo()
    ↓
run_document_extraction() / run_photo_extraction()
    ↓
get_multimodal_extraction_provider()
    ├── FIXTURE  → FixtureExtractionProvider (E2E / CI)
    └── OPENAI   → OpenAIExtractionProvider (production GPT-4o)
    ↓
validate_extraction_result()  → legacy envelope
```

**Legacy engines (wrapped, not rewritten):**
- Images: `scheduling/photo_router_service._openai_parse_photo_impl()` (GPT-4o vision)
- Documents: `scheduling/document_router_service._openai_parse_document_impl()` (text extract + GPT-4o classify)

**Server config:** `MULTIMODAL_EXTRACTION_PROVIDER` — `FIXTURE` | `OPENAI` | unknown → fail closed. Never from user/OCR input.

**Normalization:** `miya/services/document_intelligence.normalize_structured_fields()`

**Typed contract:**
- Request: `MultimodalExtractionRequest` (media only — no tenant/establishment)
- Response: `MultimodalExtractionResult` (extraction data only — no mutation commands)

Forbidden in provider `structured_fields`: `create_invoice`, `create_incident`, `create_compliance_record`, `create_task`, `create_reminder`, `create_template`.

**Observability:** `MIYA_EXTRACTION_TRACE` log — provider, model, duration, success/failure (no raw bytes or full OCR text).

### 3. Operational context (C — extend)

**Builder:** `miya/services/intelligence/multimodal.py` → `build_multimodal_context()`

Loads `TenantDocument` by `attachment_ids`, produces:
- `AttachmentInsight` (kind, vendor, amount, expiry, structured)
- `reasoning_hint` with `ocr_is_not_final_intelligence: true`
- `suggested_intent` / `suggested_entity` (hint only — classifier may override)

**Phase 14.11 additions:** current linked entity state (invoice status, compliance expiry from DB), recent audit events, confidence scores.

### 4. Miya intelligence (C — reuse spine)

```
run_miya_chat()
  → build_multimodal_context()
  → run_copilot_turn()
      → unified_understand() + _apply_multimodal()
      → authorize_mutation()
      → try_planning_engine() / compound_execution
          → multimodal workflows
          → execute_structured_action()
          → verify + audit
```

**Workflows** (`planning/multimodal_workflows.py`):

| Workflow | Trigger | Action |
|----------|---------|--------|
| `incident_from_media` | Photo + report intent | create → attach → route |
| `invoice_from_media` | Invoice image + vendor/amount | record_invoice (CLARIFY if missing) |
| `compliance_reminder_from_media` | Insurance PDF + remind | sync_compliance_reminder |
| `document_processing` | "Show me the insurance" | retrieve_document (read) |
| `incident_lookup` | Photo + find incident | get_current_incident |

### 5. Retrieval (B/C — reuse)

**Tools:** `find_documents`, `get_document`, `show_document`, `query_document_intelligence`

**Implementation:** `miya/services/ops/documents.py`

`show_document` must return actual file reference for the channel (presigned URL / WhatsApp media send) — not OCR text alone when user asked for the document.

### 6. Entity linking (Phase 14.3.2 — implemented)

**Module:** `miya/services/intelligence/document_entity_linking.py` → `resolve_document_reference()`

States: `RESOLVED`, `AMBIGUOUS`, `NOT_FOUND`, `NOT_APPLICABLE`.

Read-only at reasoning time — filename/recency alone cannot authorize mutation targets.

Links stored on `TenantDocument`:
- `invoice` FK
- `compliance_document` FK
- `location` FK (establishment)

Miya links at reasoning time via `record_invoice(document_id=...)`, `attach_incident_photo(document_id=...)`, etc.

**DB state is authoritative** — extracted vendor name does not override paid invoice status in DB.

---

## Provider abstraction (Phase 14.3.4 — COMPLETE)

| Provider | Config | Role |
|----------|--------|------|
| `OpenAIExtractionProvider` | `OPENAI` (default) | GPT-4o vision + document classify via legacy impl |
| `FixtureExtractionProvider` | `FIXTURE` | Deterministic E2E boundary (Phase 14.3.3) |
| Fish Audio | N/A | Voice STT/TTS only |
| OpenAI Whisper | N/A | STT fallback |
| Mastra | N/A | Delegates to Django tools — no second agent runtime |

**Selection:** `get_multimodal_extraction_provider()` — server settings only. Unknown → `ProviderConfigurationError`.

**Integration test:** `miya/tests/integration/test_openai_extraction_provider.py` — optional, requires `OPENAI_API_KEY` + `RUN_OPENAI_EXTRACTION_INTEGRATION=1`.

---

## Security

Every read/mutation enforces:
- `tenant_id` (restaurant)
- `establishment_id` (location scope)
- `role` + entity permission

Implemented in `ops/documents.py`, `ops/context.py`, `ops/scoping.py`.

Phase 14.12: prove cross-establishment denial in multimodal E2E.

---

## Known bypasses (Phase 14.2 status)

| ID | Path | Status |
|----|------|--------|
| D1 | `_promote_linked_records()` on upload | **FIXED** — gated; default off |
| D2/D3 | `parse_photo`/`parse_document` auto_create | **FIXED** — default false |
| D4 | WhatsApp compliance fast-path skip turn | **FIXED** — always enqueue Miya |
| D5 | Agent loop parse tools with explicit auto_create=true | **FIXED** — ignored; parameter removed from tool schema |
| Legacy | `import_processes` bulk checklist import | **FIXED (14.2.1)** — preview only on agent path |
| D-path | `agent_import_process_templates` | **FIXED (14.2.2)** — authorize/verify/audit/idempotency |

---

## Document versioning (Phase 14.3.1 — implemented)

`TenantDocument`: `content_hash`, `supersedes`, `document_family_id`, `version_number`, `is_current`, `processing_status`.

Automatic supersede inference deferred to a future phase.

---

## Observability (Phase 14.3.4 — partial)

- `TurnTrace` (Phase 12), `MIYA_PIPELINE` logs, `OperationalEvent`
- `MIYA_EXTRACTION_TRACE` — provider, model, duration, category, operation_id (no document content)

---

## Test strategy

| Tier | Purpose | Location |
|------|---------|----------|
| Extraction | OCR accuracy, field parsing | Unit + fixture PDFs/images |
| Reasoning | Intent, CLARIFY on missing fields | Planning eval |
| Operational E2E | Upload → Miya → DB verify | `miya/tests/e2e/` + Postgres |
| Security | Cross-establishment denial | E2E |

See [`MULTIMODAL_E2E.md`](MULTIMODAL_E2E.md) for planned E2E workflows.

---

## What not to do

- Do not create a parallel intelligence runtime for documents
- Do not duplicate S3 storage or TenantDocument model
- Do not rebuild GPT-4o prompts from scratch without wrapping existing routers
- Do not skip verify/audit for "convenience" on uploads
- Do not claim OCR success equals operational intelligence
