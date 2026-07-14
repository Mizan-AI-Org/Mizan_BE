#!/usr/bin/env bash
# Push miya-space-persona.txt to the Miya Super Agent Space on Lua (production).
#
# Usage:
#   ./push-space-persona.sh
#   SPACE_ID=space_xxx ./push-space-persona.sh
#
# Requires LUA_API_KEY in my-agent/.env or mizan-backend/.env

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MAIN_AGENT="$BACKEND_DIR/my-agent"
PERSONA_FILE="$SCRIPT_DIR/miya-space-persona.txt"
SPACE_ID="${SPACE_ID:-space_1781075137689_i0wbi3v}"
API_URL="${LUA_API_URL:-https://api.heylua.ai}"

log() { printf "\n\033[1;36m▶ %s\033[0m\n" "$*"; }
ok()  { printf "\033[1;32m✓ %s\033[0m\n" "$*"; }
err() { printf "\033[1;31m✗ %s\033[0m\n" "$*" >&2; }

if [[ ! -f "$PERSONA_FILE" ]]; then
  err "Missing persona file: $PERSONA_FILE"
  exit 1
fi

if [[ -f "$MAIN_AGENT/.env" ]]; then
  # shellcheck disable=SC1091
  set -a
  source "$MAIN_AGENT/.env" 2>/dev/null || true
  set +a
elif [[ -f "$BACKEND_DIR/.env" ]]; then
  set -a
  source "$BACKEND_DIR/.env" 2>/dev/null || true
  set +a
fi

if [[ -z "${LUA_API_KEY:-}" ]]; then
  err "LUA_API_KEY not set. Run: cd mizan-backend/my-agent && npx lua auth configure"
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  err "jq is required (brew install jq)"
  exit 1
fi

log "Creating Space persona version for $SPACE_ID ..."
PAYLOAD="$(jq -n --rawfile persona "$PERSONA_FILE" '{persona: $persona}')"
CREATE_RESP="$(curl -sS -X POST \
  "$API_URL/developer/agents/$SPACE_ID/persona/version" \
  -H "Authorization: Bearer $LUA_API_KEY" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")"

VERSION="$(echo "$CREATE_RESP" | jq -r '.data.version // .version // empty')"
if [[ -z "$VERSION" || "$VERSION" == "null" ]]; then
  err "Failed to create persona version"
  echo "$CREATE_RESP" | jq . 2>/dev/null || echo "$CREATE_RESP"
  exit 1
fi
ok "Created persona version $VERSION"

log "Deploying persona version $VERSION to production ..."
DEPLOY_RESP="$(curl -sS -X POST \
  "$API_URL/developer/agents/$SPACE_ID/persona/version/$VERSION" \
  -H "Authorization: Bearer $LUA_API_KEY" \
  -H "Content-Type: application/json")"

SUCCESS="$(echo "$DEPLOY_RESP" | jq -r '.success // .status // empty')"
if [[ "$SUCCESS" != "true" && "$SUCCESS" != "success" ]]; then
  err "Deploy may have failed — check response:"
  echo "$DEPLOY_RESP" | jq . 2>/dev/null || echo "$DEPLOY_RESP"
  exit 1
fi

ok "Miya Space persona deployed (space: $SPACE_ID, version: $VERSION)"
echo ""
echo "Verify: https://heylua.ai/agent?agentId=$SPACE_ID"
