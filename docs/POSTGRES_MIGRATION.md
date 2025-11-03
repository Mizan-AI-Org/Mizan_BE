# PostgreSQL Migration Plan

This document outlines the steps to migrate the application database from SQLite to PostgreSQL and operate the app solely on PostgreSQL.

## Overview
- Remove SQLite configuration from the app and standardize on PostgreSQL.
- Use `DATABASE_URL` or discrete env vars for connection.
- Enable Django persistent connections (`CONN_MAX_AGE`) and `ATOMIC_REQUESTS`.
- Provide tooling for sequence reset, backups, and data consistency.

## Prerequisites
- PostgreSQL server accessible (host/port/user/password/database).
- `psycopg2-binary` installed.
- Optional: `pgloader` installed for one-shot migration from SQLite.

## Configuration
- Update `.env`:
  - `DATABASE_URL=postgres://USER:PASS@HOST:PORT/DBNAME`
  - or set `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`.
  - Optional: `POSTGRES_SSLMODE=prefer` and `PG_CONN_MAX_AGE=60`.

## Data Migration (SQLite → PostgreSQL)
1. Stop the app.
2. Backup SQLite file.
3. Use pgloader to migrate:
   - `./scripts/migrate_sqlite_to_postgres.sh /path/to/sqlite.db`
4. Reset sequences:
   - `python manage.py reset_sequences`
5. Apply migrations (if needed):
   - `python manage.py migrate`

## Verification
- Run consistency check:
  - `python manage.py db_consistency_check`
- Manually test core flows (login, CRUD endpoints).

## Performance Benchmarks
- Enable persistent connections via `CONN_MAX_AGE`.
- Run basic timings with Django debug toolbar or simple `timeit` around hot queries.
- Check index usage: ensure primary/foreign keys exist and add needed indexes.

## Error Handling & Transactions
- `ATOMIC_REQUESTS=True` ensures each request runs within a transaction.
- Handle connection errors with retries at the application level where necessary.

## Automatic Synchronization
- The application now writes directly to PostgreSQL. No dual-write; all operations persist immediately within transactions.

## Backup & Recovery
- Use `./scripts/backup_postgres.sh` to create compressed dumps.
- Restore with `pg_restore -d DBNAME dumpfile.dump`.

## Security
- Use strong credentials and restrict DB user permissions.
- Prefer SSL (`POSTGRES_SSLMODE=verify-full`) in production with valid certificates.
- Do not check `.env` into version control.

## Notes
- If any tables were created manually or sequences drifted, run `reset_sequences`.
- Monitor connection pool size via `CONN_MAX_AGE` and server `max_connections`.