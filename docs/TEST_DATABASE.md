# PostgreSQL Test Database Strategy

## Problem

The default Django test runner creates an isolated database per run using `CREATE DATABASE`. In environments where the PostgreSQL role lacks `CREATEDB`, tests fail with:

```
permission denied to create database
```

This is **expected** in locked-down local/CI Postgres roles and must **not** be worked around by granting production database users `CREATEDB`.

## Required Permissions (CI / dedicated test role)

| Permission | Purpose |
|------------|---------|
| `CONNECT` on `postgres` or admin DB | Connect to server |
| `CREATE` on schema `public` in **pre-created test DB** | Run migrations |
| `SELECT/INSERT/UPDATE/DELETE` on test tables | Integration tests |
| **NOT** `CREATEDB` on production roles | Security boundary |

### Recommended CI pattern

1. **Pre-provision** a database: `mizan_test` (or `mizan_test_<job_id>` created once by infra).
2. Configure Django to use it **without** auto-create:

```python
# mizan/test_settings_postgres.py
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["TEST_DB_NAME"],  # e.g. mizan_test
        "USER": os.environ["TEST_DB_USER"],
        "PASSWORD": os.environ["TEST_DB_PASSWORD"],
        "HOST": os.environ.get("TEST_DB_HOST", "localhost"),
        "PORT": os.environ.get("TEST_DB_PORT", "5432"),
        "TEST": {
            "NAME": os.environ["TEST_DB_NAME"],  # reuse same DB; no CREATE DATABASE
            "MIRROR": None,
        },
    }
}
```

3. Run migrations before tests: `python manage.py migrate --settings=mizan.test_settings_postgres`
4. Optionally truncate between suites or use `--keepdb` when schema unchanged.

## Local development options

| Mode | Settings module | When to use |
|------|-----------------|-------------|
| Unit / SimpleTestCase | default / `test_settings_sqlite.py` | Fast regression; no real DB |
| SQLite in-memory | `mizan.test_settings_sqlite` | Local without Postgres CREATEDB; some migrations may fail on SQLite-only syntax |
| Real Postgres integration | `mizan.test_settings_postgres` (to add) | E2E against real schema; requires pre-created `mizan_test` |

## Migration strategy

- Apply the same migration chain as production against the test database.
- Do not maintain a separate schema fork for tests.
- Domain integration tests (tasks, incidents, scheduling Wave 1, operational audit) require Postgres features (JSONB, constraints) — treat SQLite as **unit-only**.

## Seed data

- Use factory/fixture helpers per app (`miya/tests/fixtures/`, domain test modules).
- Eval simulation uses in-memory `WorldEntity` fixtures — no DB required for planning-tier eval.
- E2E seeds: minimal tenant + establishment + staff + sample tasks/incidents created in `setUpTestData`.

## CI execution (target)

```yaml
services:
  postgres:
    image: postgres:16
    env:
      POSTGRES_USER: mizan_test
      POSTGRES_PASSWORD: test
      POSTGRES_DB: mizan_test
steps:
  - run: python manage.py migrate --settings=mizan.test_settings_postgres
  - run: python manage.py test miya.tests scheduling.tests --settings=mizan.test_settings_postgres --keepdb
```

## Current status (Phase 12.5)

| Suite | Status |
|-------|--------|
| Unit / SimpleTestCase regression (Phase 11 + 12) | **PASS** (119 tests) |
| **Real PostgreSQL E2E** (`miya.tests.e2e`) | **PASS** (66 tests) |
| SQLite full migrate + test | **BLOCKED** — some migrations use Postgres-only SQL |

## Exact commands (local)

```bash
# One-time setup
createdb mizan_test   # or: psql -c "CREATE DATABASE mizan_test;"
cd mizan-backend
python manage.py migrate --settings=mizan.test_settings_postgres

# Run E2E (real PostgreSQL, reuses mizan_test — no CREATE DATABASE)
python manage.py test miya.tests.e2e --settings=mizan.test_settings_postgres --keepdb -v 2

# Run unit regression (no Postgres required)
python manage.py test \
  miya.tests.test_phase12_intelligence \
  miya.tests.test_phase11_option_c_audit \
  miya.tests.test_phase11_verification \
  miya.tests.test_message_pipeline \
  miya.tests.test_planning_phase3 \
  miya.tests.test_copilot_phase10 \
  miya.tests.test_eval_phase9 \
  --settings=mizan.test_settings
```

Environment variables (optional overrides):

```bash
export TEST_DB_NAME=mizan_test
export TEST_DB_USER=macbookpro
export TEST_DB_HOST=localhost
export TEST_DB_PORT=5432
```

**Do not grant CREATEDB to production database roles.** Pre-provision `mizan_test` instead.
