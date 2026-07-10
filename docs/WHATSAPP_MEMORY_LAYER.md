# WhatsApp-first memory layer (Memorae-parity)

Miya can **save, recall, remind, and resurface** knowledge on WhatsApp while keeping restaurant ops intact.

## Who uses it

**Managers and staff — both on WhatsApp from their own phone.**

The manager’s WhatsApp contact with Miya is their personal Memorae:
- Save / recall notes (private by default)
- Personal lists
- Reminders that fire back to their WhatsApp
- Morning briefing
- Plus full ops (assign, chase, invoices) in the same chat

The dashboard remains for widgets and multi-person oversight — it is not required for personal memory.

## Phase 1 — Personal / team memory

| Capability | WhatsApp example | Tool / API |
|---|---|---|
| Save note | “Save this idea for Brand X Ramadan” | `knowledge_memory` → `POST /api/scheduling/agent/memory-notes/` |
| Recall | “What did we plan for Brand X Ramadan?” | `knowledge_memory` recall → `GET .../memory-notes/?q=` |
| Lists | “Add milk to shopping” | `memory_list` → `POST .../memory-lists/` |
| WA reminder | “Remind me Friday 10h to call accountant” | `personal_whatsapp_reminder` → Celery `personal_reminder_sweep` |
| Daily briefing | “Brief me” / auto 07:30 | `daily_briefing` + `daily_briefing_sweep` |
| Serendipity | “Anything I forgot?” / Sunday resurface | `knowledge_memory` serendipity + `serendipity_sweep` |

Ops preferences stay on `agent_memory` (scheduling corrections). Knowledge notes are `MemoryNote`.

## Phase 2 — Ops gaps

| Capability | Tool / API |
|---|---|
| Manager validation (any category, non-blocking) | `validate_task` · `requires_manager_validation` on create |
| Photo proof of work | `submit_task_proof` |
| Search tasks / staff + assignments | `ops_search` |
| Free-text check-in (“I’ll be late”) | `classify_checkin_message` |
| Order station auto-detect | `detect_order_station` |
| Department default assignees | Existing `category_owners` + `GET/POST .../department-owners/` |
| Absent assignee flag (no auto-reassign) | `assignee_absent` on widget serialize + search |

## Models

- `scheduling.MemoryNote`, `MemoryList`, `MemoryListItem`, `PersonalReminder`
- `dashboard.Task`: `requires_manager_validation`, `manager_validated_*`, `proof_*`, `require_photo_proof`
- `dashboard.StaffCapturedOrder`: `requires_manager_validation`, `detected_station`

## Deploy

```bash
# Backend
cd mizan-backend && .venv/bin/python manage.py migrate scheduling dashboard

# Redeploy Miya (tools registered in staff-orchestrator skill)
cd my-agent && lua deploy   # or your swarm deploy script
```

Celery beat already includes reminder / briefing / serendipity sweeps after settings reload.
