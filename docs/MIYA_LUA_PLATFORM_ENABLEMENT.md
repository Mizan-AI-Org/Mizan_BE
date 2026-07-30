# Miya — Lua platform enablement checklist

Most super-agent capabilities are **already in code** under `mizan-backend/my-agent`. This doc is what to flip on in Lua Admin / env so they actually run in production.

## 1. Platform features (RAG / web / inquiry)

```bash
cd mizan-backend/my-agent
lua features enable --feature-name rag
lua features enable --feature-name webSearch
lua features enable --feature-name inquiry
```

Upload SOPs/menus/policies via Admin → Knowledge, or see `resources/README.md`.

## 2. Specialist swarm env vars

Set on the **base Miya** agent (Lua env):

| Variable                   | Specialist            |
| -------------------------- | --------------------- |
| `MIYA_OPS_AGENT_ID`        | miya-ops-agent        |
| `MIYA_FINANCE_AGENT_ID`    | miya-finance-agent    |
| `MIYA_HR_AGENT_ID`         | miya-hr-agent         |
| `MIYA_COMMS_AGENT_ID`      | miya-comms-agent      |
| `MIYA_INTEL_AGENT_ID`      | miya-intel-agent      |
| `MIYA_FACILITIES_AGENT_ID` | miya-facilities-agent |

Without these, `delegate_to_specialist` returns `not_configured` and Miya falls back to her own tools (still works).

## 3. Spaces (recommended production entry)

In [Lua Admin → Spaces](https://admin.heylua.ai/): create **Miya Space** supervising base Miya + the six specialists. Point WhatsApp channel + `VITE_LUA_AGENT_ID` / LuaPop at the **Space** ID.

## 4. Model

Optional on deploy host / Lua env:

```bash
MIYA_MODEL=google/gemini-2.5-flash   # default
# MIYA_MODEL=anthropic/claude-sonnet-4-6
# MIYA_MODEL=openai/gpt-5.4
```

## 5. Jobs notify recipients

For daily/weekly/overdue chase jobs that need a manager list:

```bash
MIYA_MANAGER_USER_IDS=luaUserId1,luaUserId2
MIYA_HEALTH_ALERTS=1   # optional alerts from miya-health-check
```

## 6. WhatsApp Flows / Templates

- Flows: set `LEAVE_REQUEST_FLOW_ID`, `INCIDENT_REPORT_FLOW_ID`, `CLOCK_IN_FLOW_ID` (defaults exist for leave/incident).
- Templates: use `list_whatsapp_templates` / `send_whatsapp_template` after Meta approval.

## 7. LuaPop (dashboard)

Already mounted via `LuaWidget` in `DashboardLayout` + `StaffGridLayout`. Requires:

- `VITE_LUA_AGENT_ID` (Space or base agent)
- Domain whitelist in Lua Chat Widget settings (localhost needs `environment: "production"` inline — already handled in widget)

## 8. Deploy

```bash
cd mizan-backend/my-agent
lua push   # or your usual deploy pipeline
```

Confirm jobs appear: `lua jobs view`
