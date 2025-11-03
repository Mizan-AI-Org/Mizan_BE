#!/usr/bin/env bash
set -euo pipefail

# Simple Postgres backup script
# Usage: ./scripts/backup_postgres.sh [output_dir]

OUTPUT_DIR=${1:-backups}
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
mkdir -p "$OUTPUT_DIR"

if [[ -n "${DATABASE_URL:-}" ]]; then
  echo "Using DATABASE_URL"
  pg_dump "$DATABASE_URL" -Fc -f "$OUTPUT_DIR/mizan_${TIMESTAMP}.dump"
else
  DB_NAME=${POSTGRES_DB:-mizan}
  DB_USER=${POSTGRES_USER:-postgres}
  DB_HOST=${POSTGRES_HOST:-localhost}
  DB_PORT=${POSTGRES_PORT:-5432}
  echo "Using discrete env vars: $DB_USER@$DB_HOST:$DB_PORT/$DB_NAME"
  PGPASSWORD=${POSTGRES_PASSWORD:-} pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -Fc -f "$OUTPUT_DIR/mizan_${TIMESTAMP}.dump"
fi

echo "Backup written to $OUTPUT_DIR/mizan_${TIMESTAMP}.dump"