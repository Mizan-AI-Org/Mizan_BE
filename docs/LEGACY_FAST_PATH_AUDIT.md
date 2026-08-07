# Legacy Fast-Path Audit — Phase 12.5

Audit of `miya/services/agent.py` and intelligence entry points for bypass risk.

## Entry point order (`run_miya_chat`)

1. Payroll delegation fast path
2. Staff delegation fast path
3. Schedule fast path
4. Ambiguous assign fast path (clarify only)
5. **`run_copilot_turn`** (unified intelligence — preferred)
6. Entity status fast path (read-only)
7. Pending ops fast path (read-only)
8. Manager schedule fast path (read-only)
9. Mastra / OpenAI agent loop

Copilot runs **before** late read-only fast paths. Mutations should route through copilot → planning → canonical ops.

---

## Fast paths in `agent.py`

| Function | Class | Mutates? | Verdict |
|----------|-------|----------|---------|
| `_try_manager_schedule_fast_path` | **A** Safe read-only | No | Keep — calendar/agenda lookup |
| `_try_entity_status_fast_path` | **A** Safe read-only | No | Keep — `get_dashboard_task` / `find_tasks` |
| `_try_ambiguous_assign_fast_path` | **A** Safe read-only | No | Keep — CLARIFY only, never guesses |
| `_try_pending_ops_fast_path` | **A** Safe read-only | No | Keep — Operations Live list |
| `_try_schedule_fast_path` | **A** Safe read-only | No | Keep — shift list |
| `_try_payroll_delegation_fast_path` | **B** Canonical | Yes | **C — Must migrate** to copilot/planning; uses `execute_tool("create_dashboard_task")` which passes through canonical dispatch but **bypasses copilot authorize/verify/trace** |
| `_try_staff_delegation_fast_path` | **B** Canonical | Yes | **C — Must migrate** — same concern as payroll path |

## Copilot / planning (canonical spine)

| Path | Class | Notes |
|------|-------|-------|
| `run_copilot_turn` | **B** Canonical | authorize → execute → verify → audit → notify |
| `try_planning_engine` | **B** Canonical | deterministic workflows + `execute_structured_action` |
| `execute_compound_plan` | **B** Canonical | Phase 12.5 — sequential steps with per-step results |
| `operational_search` | **A** Read-only | search/history/current state |

## Other entry points

| Location | Class | Notes |
|----------|-------|-------|
| `message_pipeline.sanitize_history` | **A** | Prevents assistant→user promotion |
| `attach_pipeline_meta` | **A** | Marks responses non-executable |
| WhatsApp webhook → `run_miya_chat` | **B** | Same spine as dashboard when copilot handles |
| Legacy `execute_tool` direct calls in fast paths | **C** | Should defer to copilot for mutations |

---

## Migration recommendations

1. **Payroll / staff delegation fast paths** — route through `run_copilot_turn` or `try_planning_engine` with CREATE/ASSIGN intent; remove direct `execute_tool` once parity verified.
2. **Entity status fast path** — already duplicated by copilot search + `get_current_entity_state`; consider routing status queries only through copilot (read-only, low risk).
3. **No dead code removed** in Phase 12.5 — paths classified only; removal requires migration proof.

---

## Bypass proof checklist

For each mutation path, must pass:

```
authorize → canonical operation → DB verify → audit → notify (if applicable) → truthful response
```

| Path | authorize | verify | audit | notify |
|------|-----------|--------|-------|--------|
| Copilot planning | ✅ | ✅ | ✅ | ✅ (domain) |
| Compound execution | ✅ | ✅ | ✅ | ✅ (step 2) |
| Payroll delegation fast path | ⚠️ partial | ⚠️ via tool | ⚠️ via tool | ⚠️ |
| Staff delegation fast path | ⚠️ partial | ⚠️ via tool | ⚠️ via tool | ✅ WhatsApp |

**Conclusion:** Copilot/planning spine is production-grade. Two delegation fast paths remain **C — must migrate** to full copilot routing in a future hardening pass.
