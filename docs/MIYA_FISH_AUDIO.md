# Miya — Fish Audio AI Agent on Mizan AI (SaaS)

Miya is Mizan's built-in AI operations assistant for **multi-tenant SaaS**. Every business on Mizan shares one platform WhatsApp number; Miya resolves who is messaging and what they may do from **phone → user → restaurant → RBAC**.

## Product model

| Layer | What it does |
|-------|----------------|
| **Shared WhatsApp** | `+212784476751` (`WHATSAPP_ACTIVATION_WA_PHONE`) — all staff & managers message this number |
| **Tenant resolution** | Inbound phone → `CustomUser` → `restaurant` (workspace) |
| **Miya brain** | OpenAI (`MIYA_CHAT_MODEL`) + Mizan agent tool APIs |
| **Miya voice** | [Fish Audio](https://fish.audio/app/) TTS for WhatsApp voice notes & dashboard |
| **RBAC** | `accounts/rbac_catalog.py` actions gate which tools Miya may call per role |

## Channels

1. **WhatsApp** — primary for staff (`MIYA_WHATSAPP_ENABLED=True`)
2. **Dashboard widget** — `MiyaWidget` → `POST /api/miya/chat/` for managers

## Architecture

```
Meta WhatsApp (+212784476751)
  → notifications/views.py::whatsapp_webhook
      → phone → user → restaurant (ONE-TAP activation if new)
      → Django-owned flows (clock-in, incidents, orders, checklists)
      → miya/services/whatsapp.py::handle_miya_whatsapp_turn
          → miya/services/agent.py (OpenAI + RBAC-filtered tools)
          → /api/*/agent/* (CRUD on live tenant data)
          → Fish Audio TTS (optional voice reply)
      → send_whatsapp_text / send_whatsapp_audio
```

## RBAC tool mapping

| Tool | Required action |
|------|-----------------|
| `my_shifts`, `platform_knowledge`, `staff_clock_in/out`, `staff_request`, `request_time_off`, `report_incident`, `get_business_context` | *(any Miya user)* |
| `staff_lookup`, `send_announcement`, `list/approve/reject_staff_request`, `recognize_staff` | `miya_full_tools` |
| `list_shifts`, `create_shift`, `mark_no_show`, `assign_coverage` | `edit_schedule` |
| `create_dashboard_task`, `dashboard_widgets_add` | `manage_widgets` |
| `list_inventory`, `report_waste` | `edit_inventory` |
| `proactive_insights`, `sales_summary` | `run_reports` |

Persona + playbook live in `miya/persona.py` (Super Agent). Channel tone is injected per turn (`whatsapp` vs `dashboard`).

Managers configure permissions at **Settings → RBAC**. Privileged roles (`OWNER`, `ADMIN`, `SUPER_ADMIN`) have full access.

## Environment

```bash
# Fish Audio — Miya voice
# reference_id default = "Sarah": warm, gentle, friendly female voice.
# s2.1-pro is cross-lingual — the same voice speaks EN/FR/AR/Darija from
# whatever language the reply text is in. Browse alternatives at
# https://fish.audio/discover and swap the reference_id if you want a
# different voice.
FISH_AUDIO_API_KEY=
FISH_AUDIO_REFERENCE_ID=933563129e564b19a115bedd57b7406a
FISH_AUDIO_MODEL=s2.1-pro

# Miya agent
OPENAI_API_KEY=
MIYA_CHAT_MODEL=gpt-4o-mini
MIYA_WHATSAPP_ENABLED=True
MIYA_WHATSAPP_VOICE_DEFAULT=False   # set True for Fish Audio voice notes by default

# Shared Mizan WhatsApp (Meta API uses PHONE_NUMBER_ID; this is the dialable number)
WHATSAPP_ACTIVATION_WA_PHONE=212784476751
MIYA_MASTRA_API_KEY=                  # agent API auth for tool calls
```

## API

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `POST /api/miya/chat/` | JWT | Dashboard chat |
| `POST /api/miya/voice/` | JWT | Fish Audio TTS |
| `GET /api/miya/config/` | JWT | Widget config |
| WhatsApp webhook | Meta | Inbound messages → Miya |

Legacy Mastra swarm code: [github.com/Mizan-AI-Org/Miya](https://github.com/Mizan-AI-Org/Miya) (optional; disabled when `MIYA_WHATSAPP_ENABLED=True`).
