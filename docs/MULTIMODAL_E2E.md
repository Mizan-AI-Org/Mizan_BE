# Multimodal E2E Test Plan

**Status:** PLANNED (Phase 14) — infrastructure ready from Phase 12.5  
**Related:** [`TEST_DATABASE.md`](TEST_DATABASE.md), [`PHASE_14_ARCHITECTURE_AUDIT.md`](../PHASE_14_ARCHITECTURE_AUDIT.md)

---

## Infrastructure (ready)

Phase 12.5 established:

- Pre-provisioned PostgreSQL database: `mizan_test`
- Settings: `mizan.test_settings_postgres`
- Harness: `miya/tests/e2e/harness.py` (`MiyaE2EHarness`, `PostgresE2ETestCase`)
- Seeding: `miya/tests/e2e/seed.py` (`E2EWorld`, `seed_single_establishment`, etc.)

**Run existing E2E (text-only, 66 tests):**
```bash
cd mizan-backend
python manage.py test miya.tests.e2e --settings=mizan.test_settings_postgres --keepdb -v 2
```

**Multimodal E2E:** NOT YET IMPLEMENTED — do not claim as passing.

---

## Phase 14 minimum real-DB workflows (10)

| # | Workflow | Assert |
|---|----------|--------|
| 1 | Insurance PDF → extract expiry → reminder | Reminder row exists, verified=True, audit event |
| 2 | Invoice PDF → record → approve lifecycle | Invoice status from DB; history from OperationalEvent |
| 3 | Incident photo → create → route → notify | SafetyConcernReport + notification |
| 4 | Document upload → retrieve later | Same `document_id`, file URL accessible |
| 5 | WhatsApp PDF → Miya answer | TenantDocument source=WHATSAPP, structured fields |
| 6 | Dashboard upload → WhatsApp query | Cross-channel same document row |
| 7 | Multi-establishment isolation | Staff A denied doc from establishment B |
| 8 | Replace document version | Current doc resolves to latest (after 14.13 schema) |
| 9 | Current state vs extraction | DB status overrides stale OCR in answer |
| 10 | Historical question | "Who approved?" → audit history, not conversation |

Every mutation test must assert:
1. `verified=True` on copilot/planning result
2. DB state matches expected
3. `OperationalEvent` recorded (where applicable)
4. Response does not claim success on failure

---

## Harness extensions (Phase 14.20)

Add to `MiyaE2EHarness`:

```python
def upload_document(self, file_bytes, *, filename, mime_type, channel="dashboard") -> TenantDocument
def assert_document_fields(self, doc_id, **expected_structured)
def assert_no_promotion_on_upload(self, doc_id)  # no Invoice/Compliance created silently
def assert_audit_for_entity(self, entity_type, entity_id, event_type)
```

Use real files from `miya/tests/fixtures/multimodal/` (to be added): sample insurance PDF, invoice image, incident photo.

---

## Evaluation suite separation (Phase 14.19)

| Metric type | Count target | What it measures |
|-------------|--------------|------------------|
| Extraction tests | ~80 | Field accuracy, confidence, no fabrication |
| Reasoning tests | ~80 | Intent, CLARIFY, entity linking |
| Operational E2E | ~50 | Full spine with Postgres |
| Security tests | ~20 | Cross-tenant, cross-establishment |

**Minimum total:** 230 multimodal scenarios.

Do **not** label extraction-only tests as "intelligence tests."

---

## Failure scenarios (Phase 14.23)

Each must fail safely (no false "Done", no invented fields):

- Corrupt PDF / empty image
- Poor OCR / low confidence
- Missing expiry / multiple expiry dates
- Ambiguous invoice / wrong establishment
- Duplicate upload
- Provider timeout / storage failure

---

## CI target

```yaml
services:
  postgres:
    image: postgres:16
    env:
      POSTGRES_DB: mizan_test
      POSTGRES_USER: mizan_test
      POSTGRES_PASSWORD: test
steps:
  - run: python manage.py migrate --settings=mizan.test_settings_postgres
  - run: python manage.py test miya.tests.e2e --settings=mizan.test_settings_postgres --keepdb
  - run: python manage.py test miya.tests.e2e.multimodal --settings=mizan.test_settings_postgres --keepdb  # Phase 14
```

---

## Current gap

| Item | Status |
|------|--------|
| Text E2E | ✅ 66/66 PASS |
| Multimodal E2E | ❌ Not implemented |
| Multimodal eval (230+) | ❌ 2 planning cases only |
| Upload without auto-promote | ❌ D1 still active |

Phase 14.20 begins after D1/D2 gating (Phase 14.2–14.3).
