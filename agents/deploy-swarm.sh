#!/usr/bin/env bash
# Deploy Miya supervisor + all swarm specialist agents to Lua.
#
# Prerequisites:
#   1. Valid Lua API key — run: cd ../my-agent && npx lua auth configure
#   2. Node 20+ and npm installed
#
# Usage:
#   ./deploy-swarm.sh              # deploy specialists + supervisor
#   ./deploy-swarm.sh specialists  # specialists only
#   ./deploy-swarm.sh supervisor   # main Miya agent only

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MAIN_AGENT="$BACKEND_DIR/my-agent"
ORG_ID="c1234bab-fcdf-4a0f-966e-d09d1971e04f"

SPECIALISTS=(
  "miya-ops-agent:miya-ops"
  "miya-finance-agent:miya-finance"
  "miya-hr-agent:miya-hr"
  "miya-comms-agent:miya-comms"
  "miya-intel-agent:miya-intel"
  "miya-facilities-agent:miya-facilities"
)

MODE="${1:-all}"

log() { printf "\n\033[1;36m▶ %s\033[0m\n" "$*"; }
ok()  { printf "\033[1;32m✓ %s\033[0m\n" "$*"; }
err() { printf "\033[1;31m✗ %s\033[0m\n" "$*" >&2; }

ensure_auth() {
  log "Checking Lua CLI authentication..."
  if (cd "$MAIN_AGENT" && npx lua agents >/dev/null 2>&1); then
    ok "Lua CLI authenticated"
    return
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
  if [[ -n "${LUA_API_KEY:-}" ]]; then
    log "Configuring auth from LUA_API_KEY..."
    cd "$MAIN_AGENT" && npx lua auth configure --api-key "$LUA_API_KEY"
  else
    err "Lua auth failed. Run: cd mizan-backend/my-agent && npx lua auth configure"
    exit 1
  fi
  ok "Lua CLI authenticated"
}

copy_env() {
  local agent_dir="$1"
  if [[ ! -f "$agent_dir/.env" ]]; then
    if [[ -f "$MAIN_AGENT/.env" ]]; then
      cp "$MAIN_AGENT/.env" "$agent_dir/.env"
      ok "Copied .env to $(basename "$agent_dir")"
    fi
  fi
}

sync_production_env() {
  local agent_dir="$1"
  local label="$2"

  if [[ ! -f "$MAIN_AGENT/.env" ]]; then
    err "Missing $MAIN_AGENT/.env — cannot sync production env to $label"
    return 1
  fi

  # shellcheck disable=SC1091
  set -a
  source "$MAIN_AGENT/.env" 2>/dev/null || true
  set +a

  local api_url="${API_BASE_URL:-https://api.heymizan.ai}"
  if [[ "$api_url" == http://localhost* ]]; then
    api_url="https://api.heymizan.ai"
  fi

  local webhook_key="${LUA_WEBHOOK_API_KEY:-${WEBHOOK_API_KEY:-}}"
  local service_token="${MIZAN_SERVICE_TOKEN:-}"

  if [[ -z "$webhook_key" ]]; then
    err "LUA_WEBHOOK_API_KEY not set — skip production env sync for $label"
    return 1
  fi

  log "Syncing production env → $label"
  (cd "$agent_dir" && npx lua env production -k API_BASE_URL -v "$api_url")
  (cd "$agent_dir" && npx lua env production -k LUA_WEBHOOK_API_KEY -v "$webhook_key")
  (cd "$agent_dir" && npx lua env production -k WEBHOOK_API_KEY -v "$webhook_key")
  if [[ -n "$service_token" ]]; then
    (cd "$agent_dir" && npx lua env production -k MIZAN_SERVICE_TOKEN -v "$service_token")
  fi
  ok "Production env synced for $label"
}

init_agent_if_needed() {
  local agent_dir="$1"
  local agent_name="$2"

  if [[ ! -f "$agent_dir/lua.skill.yaml" ]]; then
    log "Initializing Lua project: $agent_name"
    # Preserve custom index.ts if already authored (init --force overwrites it).
    local saved_index=""
    if [[ -f "$agent_dir/src/index.ts" ]] && grep -q "skills: \[" "$agent_dir/src/index.ts" && ! grep -q "skills: \[\]," "$agent_dir/src/index.ts"; then
      saved_index="$(mktemp)"
      cp "$agent_dir/src/index.ts" "$saved_index"
    fi
    (cd "$agent_dir" && npx lua init \
      --agent-name "$agent_name" \
      --org-id "$ORG_ID" \
      --force)
    if [[ -n "$saved_index" && -f "$saved_index" ]]; then
      cp "$saved_index" "$agent_dir/src/index.ts"
      rm -f "$saved_index"
    fi
    ok "Created lua.skill.yaml for $agent_name"
  fi
}

extract_agent_id() {
  local agent_dir="$1"
  if [[ -f "$agent_dir/lua.skill.yaml" ]]; then
    grep -E '^  agentId:' "$agent_dir/lua.skill.yaml" | head -1 | awk '{print $2}' || true
  fi
}

deploy_project() {
  local agent_dir="$1"
  local label="$2"

  log "Compiling $label..."
  (cd "$agent_dir" && npx lua compile)

  log "Pushing $label to Lua sandbox..."
  (cd "$agent_dir" && npx lua push all --force)

  log "Deploying $label to production..."
  (cd "$agent_dir" && npx lua deploy all --force --set-version latest)

  ok "Deployed $label"
}

deploy_specialists() {
  log "Deploying ${#SPECIALISTS[@]} specialist agents..."

  for entry in "${SPECIALISTS[@]}"; do
    IFS=':' read -r dir_name agent_name <<< "$entry"
    agent_dir="$SCRIPT_DIR/$dir_name"

    if [[ ! -d "$agent_dir/src" ]]; then
      err "Missing source: $agent_dir"
      exit 1
    fi

    copy_env "$agent_dir"
    init_agent_if_needed "$agent_dir" "$agent_name"
    sync_production_env "$agent_dir" "$agent_name" || true
    deploy_project "$agent_dir" "$agent_name"
  done

  echo ""
  echo "========================================="
  echo "  SPECIALIST AGENT IDs (save these)"
  echo "========================================="
  for entry in "${SPECIALISTS[@]}"; do
    IFS=':' read -r dir_name agent_name <<< "$entry"
    agent_dir="$SCRIPT_DIR/$dir_name"
    id="$(extract_agent_id "$agent_dir")"
    env_key=""
    case "$agent_name" in
      miya-ops) env_key="MIYA_OPS_AGENT_ID" ;;
      miya-finance) env_key="MIYA_FINANCE_AGENT_ID" ;;
      miya-hr) env_key="MIYA_HR_AGENT_ID" ;;
      miya-comms) env_key="MIYA_COMMS_AGENT_ID" ;;
      miya-intel) env_key="MIYA_INTEL_AGENT_ID" ;;
      miya-facilities) env_key="MIYA_FACILITIES_AGENT_ID" ;;
    esac
    printf "  %s=%s\n" "$env_key" "${id:-NOT_FOUND}"
  done
  echo ""
  echo "Add these to mizan-backend/.env and Lua agent env (Settings → Environment)."
  echo "Then attach all 6 agents to your Miya Space in the dashboard."
}

deploy_supervisor() {
  log "Deploying main Miya supervisor (my-agent)..."
  sync_production_env "$MAIN_AGENT" "Miya" || true
  deploy_project "$MAIN_AGENT" "Miya"
  ok "Supervisor deployed: baseAgent_agent_1762796132079_ob3ln5fkl"
}

ensure_auth

case "$MODE" in
  specialists) deploy_specialists ;;
  supervisor)  deploy_supervisor ;;
  all)
    deploy_specialists
    deploy_supervisor
    ;;
  *)
    err "Unknown mode: $MODE (use: all | specialists | supervisor)"
    exit 1
    ;;
esac

echo ""
ok "Swarm deployment complete!"
echo ""
echo "Space supervisor persona (paste into Miya Space → Personality tab):"
echo "  File: $SCRIPT_DIR/miya-space-persona.txt"
echo "  Quick copy: pbcopy < $SCRIPT_DIR/miya-space-persona.txt"
echo ""
echo "Next steps:"
echo "  1. Open Miya Space → Personality → replace 'Stellar' template with miya-space-persona.txt"
echo "  2. Space → Agents tab → ensure all 7 agents are attached (Miya + 6 specialists)"
echo "  3. Set MIYA_*_AGENT_ID env vars on the supervisor (if using delegate_to_specialist)"
echo "  4. Connect WhatsApp channel to the Space (not individual agents)"
echo "  5. Test: https://heylua.ai/agent?agentId=<your-space-id>"
