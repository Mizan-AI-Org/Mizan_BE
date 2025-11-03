#!/usr/bin/env bash
set -euo pipefail

# Migrate data from a SQLite file to PostgreSQL using pgloader.
# Requirements: pgloader installed (https://pgloader.readthedocs.io)
# Usage: ./scripts/migrate_sqlite_to_postgres.sh /path/to/sqlite.db

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 /path/to/sqlite.db"
  exit 1
fi

SQLITE_PATH=$1

if ! command -v pgloader >/dev/null 2>&1; then
  echo "pgloader is not installed. Install it and rerun."
  echo "macOS (brew): brew install pgloader"
  exit 1
fi

if [[ -n "${DATABASE_URL:-}" ]]; then
  PG_URL="$DATABASE_URL"
else
  DB_NAME=${POSTGRES_DB:-mizan}
  DB_USER=${POSTGRES_USER:-postgres}
  DB_PASS=${POSTGRES_PASSWORD:-}
  DB_HOST=${POSTGRES_HOST:-localhost}
  DB_PORT=${POSTGRES_PORT:-5432}
  PG_URL="postgresql://$DB_USER:${DB_PASS}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
fi

echo "Migrating from $SQLITE_PATH to $PG_URL"
pgloader "$SQLITE_PATH" "$PG_URL"

echo "Migration complete. Consider running:"
echo "  python manage.py reset_sequences"
echo "  python manage.py check"