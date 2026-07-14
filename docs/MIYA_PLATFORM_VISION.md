# Miya Platform Vision — Central Intelligence Layer

**Status:** Living north star  
**Last updated:** July 2026  
**Related:** Scenario catalog → [`MIYA_SCENARIO_VISION.md`](./MIYA_SCENARIO_VISION.md) · Agent sync → `agents/shared/dailyScenariosPersona.ts`

---

## Vision

Miya is the **primary interface** between every user and Mizan AI. She understands the platform (features, data, workflows, permissions) and behaves by role:

| Audience | Role | Channel |
|----------|------|---------|
| **Managers** | AI **Copilot** — operations, analysis, reports, recommendations, automation | LuaPop / dashboard (primary) · WhatsApp OK |
| **Staff** | AI **Companion** — daily work, tasks, coaching, procedures, attendance | WhatsApp (primary) |

> Goal: every staff member and manager can accomplish nearly everything they need inside Mizan through natural conversation — Miya is the central intelligence layer, not a chatbot.

---

## Core requirements (product contract)

1. Understand every Mizan feature (or say honestly when not yet supported).
2. Answer questions on inventory, sales, purchases, suppliers, customers, recipes, stock, finance, staff, shifts, reports, analytics, operations.
3. Query live restaurant data via tools/APIs (never invent numbers).
4. Maintain conversation context until the task is complete.
5. Execute actions when asked (create, assign, notify, approve) — not only explain.
6. Ask clarifying questions when ambiguous (prefer **one** clarifying question).
7. Recommend actions; don’t dump raw tables without a next step.
8. Explain platform features to new users.
9. Detect intent automatically; route to the right specialist/tool.
10. Offer proactive suggestions that improve performance.
11. Respect **role-based permissions** — staff only see/do what they’re allowed.
12. **Never hallucinate data.** If unavailable, say so and offer the closest supported action.
13. Prefer live tenant data over generic advice.
14. Closed loop: create → notify → chase → confirm → close (see scenario vision Part 1b).

---

## Manager Copilot — example asks

- What are today’s sales? / Compare this month with last month.
- Which products are running low? / Recommend today’s purchases.
- Which supplier should I reorder from?
- What is our profit this week? / Explain unusual spending.
- Which staff performed best? / Schedule shifts / Create tasks / Send announcements.
- Forecast demand / Identify waste / Monitor KPIs / Generate financial reports.

**Agents:** Space → miya-finance · miya-ops · miya-hr · miya-comms · miya-intel · miya-facilities · orchestration (`my-agent`).

---

## Staff Companion — example asks

- What should I do next? / Show today’s tasks / Start my checklist.
- When is my shift? / Clock me in.
- How do I record a delivery / stock count / waste?
- Where can I find this ingredient? / Explain this screen.
- Tell my manager… (escalation → StaffRequest, never fake confirm).
- Show announcements / restaurant procedures (knowledge base).

**Channel tone:** warm, short, no dashboard jargon.

---

## Technical architecture

| Layer | Implementation |
|-------|----------------|
| Swarm | Space supervisor + 6 specialists + `my-agent` orchestration |
| Tools | Lua skills → `ApiService` → Django agent APIs → PostgreSQL (ORM) |
| Memory | Conversation thread + WhatsApp memory layer (see `WHATSAPP_MEMORY_LAYER.md`) |
| RAG | Tenant `knowledge_base` (Lua) + platform `platform_knowledge` (Django curated guides) |
| RBAC | Role resolved from JWT / staff-by-phone → tool allowlists (`agents/shared/roleGate.ts`) |
| Anti-hallucination | Deterministic preprocessors for high-frequency intents + honest tool relay |
| Digests | Celery `manager_ops_digest_sweep` → WhatsApp (21:00 daily + Sunday weekly) |

**Not in scope (by design):** agents running arbitrary SQL. Live data goes through governed agent APIs.

---

## Delivery phases

| Phase | Theme | Deliverables | Status |
|-------|--------|--------------|--------|
| **1** | North star | This doc + persona contracts (Manager Copilot / Staff Companion) | Done |
| **2** | RBAC + no hallucination | Role resolution + manager-only tool gates + invent guards | Done |
| **3** | Manager copilot slice | Deterministic sales / low-stock / purchase-recommend path | Done |
| **4** | Staff companion slice | “What should I do next?” → checklist / tasks preview | Done |
| **5a** | Recipes / BOM | `GET /api/menu/agent/food-cost/` + manager copilot “food cost / margin” | Done |
| **5b** | Platform RAG | `GET /api/agent/platform-knowledge/` + intel `platform_knowledge` tool | Done |
| **5c** | Proactive digests | Celery ops digest (staffing + requests + invoices) → WhatsApp | Done |
| **5d** | PO ↔ invoice | Invoice FK + match/confirm agent APIs + finance tools | Done |

---

## Acceptance bar

- Manager on LuaPop: “What are today’s sales?” → live totals or honest “POS unavailable”, never invented figures.
- Manager: “What’s running low?” → low-stock list from inventory API + optional reorder suggestion.
- Manager: “What’s our food cost?” → live recipe BOM % or honest “no recipes costed yet”.
- Manager: “Match this invoice to a PO” → ranked suggestions; confirm before link.
- Staff on WhatsApp: “What should I do next?” → real checklist/tasks or clear “none / clock in first”.
- Staff cannot run manager-only tools (sales reports, supplier orders, role grants) — polite redirect.
- “How does sales work in Mizan?” → `platform_knowledge` guides (not invented product claims).
- Managers with WhatsApp receive evening ops digest (opt out via `whatsapp_enabled=False`).
- Any invent-style “technical issue” without a tool call is a **defect**.

---

*Own the restaurant wedge first; reuse the same primitives (requests, tasks, invoices, shifts, messages, knowledge, follow-ups) across verticals.*
