# Miya Scenario Vision — What Managers & Staff Will Ask For

**Purpose:** A living catalog of real-world requests managers, staff, owners, suppliers, and guests will make to Miya — across industries, languages, and levels of ambition. Each scenario is tagged by **current capability** so product, engineering, and agent design can converge on one north star:

> **Say it once on WhatsApp (or voice). Miya understands, executes, confirms with proof — or asks exactly one clarifying question.**

> **Nothing stays silent.** Miya follows up on **pending** and **urgent** work through the **Mizan app** (dashboard widgets, inbox, push/in-app alerts) **and** **WhatsApp notifications to staff** — until the issue is owned, updated, or resolved.

**Audience:** Product, engineering, agent designers, QA, and pilot customers validating coverage.

**Last updated:** June 2026

---

## How to read this document

| Tag | Meaning |
|-----|---------|
| ✅ **Supported** | Works today via Miya / specialist agents + backend tools (may need deploy or OAuth setup) |
| 🟡 **Partial** | Some building blocks exist; UX is brittle, manual follow-up required, or only works for certain tenants |
| ❌ **Not yet** | No reliable end-to-end path; Miya may apologize, hallucinate, or defer to “open the dashboard” |
| 🔮 **North star** | Ambitious future state — may require new integrations, regulations, or agent specialization |

**Agents today:** Miya Space (supervisor) · miya-ops · miya-finance · miya-hr · miya-comms · miya-intel · miya-facilities · Miya orchestration (`my-agent`)

**Primary wedge today:** Restaurants, cafés, bars, and multi-site F&B operators (Morocco-first defaults: MAD, Darija, labor law, +212).

---

## Part 1 — Baseline: what works today

Before the wishlist, these are **already in scope** when deployed and configured:

| Domain | Example request | Agent / tool |
|--------|-----------------|--------------|
| Clock in/out | “Pointer” / Share Location | miya-ops · `staff_clock_in` |
| Shifts & coverage | “Who’s on tomorrow?” / swap / no-show | miya-ops |
| Invoices | “Pay the baker — 4000 MAD, due 30 July” | miya-finance · `record_invoice` |
| Purchase orders | “Order 27 bottles Aperol before Thursday” | orchestration · `staff_request` PURCHASE_ORDER |
| Maintenance | “Men’s toilets need repair” | orchestration / facilities · MAINTENANCE |
| HR reminders | “Daily reminder to prepare payslips” | miya-hr · `create_dashboard_task` |
| Leave | Staff: “I want leave next Monday” | miya-comms · WhatsApp Flow |
| Incidents | “Customer slipped — wet floor” | miya-facilities · `report_incident` |
| Announcements | “Tell the team dinner is 30 min late” | miya-comms · `inform_staff` / `send_announcement` |
| Knowledge | “What’s our allergen policy for nuts?” | miya-intel · knowledge base |
| POS / sales | “Sales yesterday vs last week” | miya-finance · Square / POS tools |
| Tasks | “Assign Karim: clean terrace before lunch” | orchestration · `create_dashboard_task` |
| Calendar | “Remind me to call accountant Friday 10h” | orchestration · Google Calendar (OAuth required) |
| Inventory count | Step-by-step count session | miya-facilities |
| Waste | “Log 3 kg tomatoes — expired” | miya-facilities |
| Recognition | “Kudos to Sara for covering Friday” | miya-hr |
| Account activation | “I’m ready to activate my account” | miya-hr · `account_activation` |
| **Task follow-ups (WhatsApp)** | Pending assigned task — Miya nudges assignee on WhatsApp | orchestration · `create_dashboard_task` + `task_follow_up_sweep` |
| **Staff request notify (WhatsApp)** | New maintenance / PO / HR request — assignee pinged on WhatsApp | orchestration · `staff_request` ingest |
| **Urgent lane (app)** | Open URGENT/HIGH items surface on `urgent_top` + category widgets | Dashboard · staff inbox |
| **SLA re-surface (app)** | Stale URGENT/HIGH requests get SLA nudge comments; WAITING_ON revived on follow-up date | `staff_request_sla_sweep` |

Everything below extends, deepens, or crosses into **new verticals**.

---

## Part 1b — Follow-ups & escalation (non-negotiable product rule)

Miya is not a “log and forget” assistant. Every actionable item must have a **closed loop**: create → notify → chase → confirm → close.

### Dual channel: app + WhatsApp

| Channel | Who | What |
|---------|-----|------|
| **Mizan app / dashboard** | Managers & assignees | Widget lanes (`urgent_top`, maintenance, staff_inbox, tasks), status changes, SLA comments, bell notifications, read receipts |
| **WhatsApp** | Staff (primary) & managers | Initial assignment ping, automatic follow-ups on pending tasks, `inform_staff` for urgent pings, templates when outside 24h window |

**Rule for agents:** When Miya creates a task or staff request with an assignee, default is **notify on WhatsApp = yes** and **follow_up_enabled = yes** (unless the manager explicitly says “don’t tell them yet”). Miya must tell the requester: *“I'll follow up automatically if they don't respond.”*

### What exists today

| Mechanism | Scope | Status |
|-----------|--------|--------|
| `create_dashboard_task` → WhatsApp to assignee | Dashboard tasks | ✅ |
| `task_follow_up_sweep` (Celery, ~15 min) | PENDING tasks · WhatsApp nudges inside 24h window | ✅ |
| Follow-up timing by priority | URGENT ~2h/8h · HIGH ~3h/10h · MEDIUM ~4h/12h · LOW ~6h/14h · max 2 nudges | ✅ |
| `staff_request` ingest → WhatsApp to assignee | New requests (maintenance, PO, etc.) | ✅ |
| `staff_request_sla_sweep` | URGENT pending >4h · HIGH pending >24h → in-app SLA comment + **`staff_request_follow_up_sweep`** WhatsApp nudges | ✅ |
| `inform_staff` | Immediate manager-initiated WhatsApp ping | ✅ |
| `agent_chase_operational_record` | Manager: “Follow up with Driss” / “Relance sur…” → immediate WhatsApp chase | ✅ |
| Manager escalation when follow-ups exhausted | `escalated_at` + in-app + WhatsApp to managers | ✅ |
| Follow-ups on **overdue invoices**, **open incidents**, **unconfirmed POs** | Finance / facilities / procurement lanes | 🟡 Partial · unified chase via staff request/task only |
| WhatsApp after 24h window | Approved templates only | 🟡 Constraint · need template library for chase messages |

### Scenario catalog — follow-ups managers will expect

| Scenario | Example | App | WhatsApp staff | Status |
|----------|---------|-----|----------------|--------|
| Assigned task, no response | “Karim hasn’t started terrace cleanup” | Task stays PENDING on Operations widget | Auto follow-up #1 at ~4h (URGENT ~2h) | ✅ tasks |
| Urgent maintenance, no owner action | “WC repair still open 6h later” | SLA comment on request · `urgent_top` | Auto WhatsApp follow-ups + manager escalation | ✅ |
| Manager asks Miya to chase | “Follow up with Driss on the Aperol order” | Task/request visible in inbox | `chase` intent → immediate WhatsApp | ✅ |
| Escalate to manager | 2 follow-ups exhausted · still PENDING | Escalation flag · urgent widget | Miya notifies managers on WhatsApp | ✅ |
| Park until date | “Waiting on supplier — check back Friday” | `WAITING_ON` + `follow_up_date` → auto re-escalate | Optional reminder to manager on date | 🟡 |
| Urgent incident | “Slip and fall — security not on scene” | Incident lane + urgent widget | Notify tagged staff + chase until acknowledged | 🟡 |
| Overdue invoice | “Baker invoice due tomorrow — unpaid” | Finance widget | Notify manager · optional AP assignee chase | ❌ |
| Read receipt | “I need proof Sara saw the message” | Dashboard delivery status | `require_read_receipt` on task | 🟡 |
| Multi-site owner digest | “Any urgent open across all sites?” | Cross-location report + urgent counts | WhatsApp summary to owner | 🟡 |

### Acceptance tests — follow-up loop

```
Manager: Assign Karim — clean terrace before lunch. Follow up if no response.
Miya:    ✓ Task TSK-xxx assigned. Karim notified on WhatsApp. I'll follow up automatically if they don't respond.

[4h later, still PENDING]
Miya→Karim (WhatsApp): Just checking in on: Clean terrace before lunch…

[Manager opens app]
Dashboard: TSK-xxx · PENDING · 1 follow-up sent · Karim · Operations lane
```

```
Manager: Il faut réparer les wc hommes — urgent
Miya:    ✓ REQ-xxx logged · URGENT · [Assignee] notified on WhatsApp.
         [If still PENDING after SLA] App shows SLA nudge; [future] WhatsApp chase to assignee + manager escalation.
```

### Gaps to close (engineering)

1. ~~**Staff-request WhatsApp follow-ups**~~ — ✅ `staff_request_follow_up_sweep` (Celery, every 15 min)
2. ~~**Manager escalation**~~ — ✅ when max follow-ups hit on tasks + staff requests
3. ~~**Default flags**~~ — ✅ `follow_up_enabled=true` on operational staff requests; tasks default on unless personal reminder
4. ~~**Unified “chase” intent**~~ — ✅ preprocessor + `POST /api/staff/agent/records/chase/`
5. **Template fallback** — pre-approved WhatsApp templates for follow-ups outside the 24h window
6. **In-app push** — mobile/web push when urgent item ages without owner action (bell exists; push depth varies)
7. ~~**Honest relay**~~ — ✅ `whatsapp_sent`, follow-up counts, escalation in API responses

---

## Part 2 — Food & beverage (core vertical)

### 2.1 Restaurant & café operations

| Scenario | Example message | Status | Gap / what’s needed |
|----------|-----------------|--------|---------------------|
| Dynamic menu pricing | “Raise all pasta +10 MAD this weekend only” | ❌ | Menu CMS, POS sync, approval workflow, rollback |
| 86 / sold out | “We’re out of sea bass — stop selling it everywhere” | 🟡 | POS item availability API; auto-update delivery apps |
| Recipe costing | “What’s our margin on the lunch special if lamb goes up 8%?” | ❌ | Recipe BOM, supplier price feeds, margin engine |
| Allergen service script | “Guest at table 7 has celiac — what can the kitchen make?” | 🟡 | KB helps; need live menu + cross-contamination rules |
| Table turns & pacing | “We’re 40 covers behind — push desserts, delay starters on 12–15” | ❌ | Reservation + floor plan + KDS integration |
| VIP arrival | “Mr. Benjelloun arrives at 20:30 — champagne, quiet table” | 🟡 | Reservation note exists; no proactive staff briefing chain |
| Comp & void approval | “Comp two coffees for wait — manager code” | ❌ | POS void/comp with WhatsApp manager approval |
| Tip pooling | “Split tonight’s tips: 60% floor, 40% kitchen” | ❌ | POS tip export + payroll rules engine |
| Health inspection prep | “Generate HACCP checklist for tomorrow’s visit” | 🟡 | Checklists exist; no inspection-specific pack |
| Ramadan / seasonal mode | “Switch to Ramadan hours and iftar menu Friday” | 🟡 | Referenced in persona; not full automated switch |
| Ghost kitchen routing | “Route Uber orders to Kitchen B only tonight” | ❌ | Multi-brand order router |
| Food truck geofence | “Start service when truck arrives at Souk location” | ❌ | Mobile geofence + pop-up shift model |

### 2.2 Bar & nightlife

| Scenario | Example | Status | Gap |
|----------|---------|--------|-----|
| Pour cost alert | “Alert me if Aperol variance > 2% this week” | ❌ | Inventory depletion vs sales reconciliation |
| Last call cascade | “Last call in 20 min — notify bar + floor” | 🟡 | `inform_staff` works; no scheduled cascade |
| ID / age verification log | “Log refused entry — no ID” | ❌ | Compliance log + optional photo |
| DJ / event run sheet | “Tonight: DJ at 23:00, door 50 MAD, 2 security” | ❌ | Event object linked to staffing + tasks |

### 2.3 Catering & events

| Scenario | Example | Status | Gap |
|----------|---------|--------|-----|
| Banquet BEO | “Wedding 200 pax Saturday — full briefing pack” | ❌ | BEO document model, staffing calculator, prep list |
| Dietary matrix | “30 vegan, 15 halal, 5 nut-free — production sheet” | ❌ | Allergen/diet engine tied to production |
| Equipment rental | “Rent 200 chairs — track delivery + return” | ❌ | Asset rental module |
| Deposit & cancellation | “Client cancelled — apply 50% policy” | ❌ | Contract terms + payment linkage |

---

## Part 3 — Hospitality (hotels, riads, resorts)

Mizan has **housekeeping tags** and room-ready language but is **not yet a PMS**.

| Scenario | Example | Status | Gap |
|----------|---------|--------|-----|
| Room status | “Mark 204 clean, 205 dirty, 206 DND” | ❌ | PMS / housekeeping board integration |
| Turn-down request | “Guest in 312 wants extra towels at 21:00” | ❌ | Guest request → HK task with SLA |
| Minibar restock | “Restock minibar on checkout for 401–410” | ❌ | Minibar inventory per room |
| Early check-in fee | “Allow 12:00 check-in — charge 300 MAD” | ❌ | PMS + payment |
| Group block | “Block 15 rooms for Atlas Conference 12–14 Sept” | ❌ | Group booking engine |
| Concierge via WhatsApp | Guest texts hotel number: “Book hammam at 17:00” | 🔮 | Guest-facing Miya + supplier booking |
| Night audit | “Run night audit and send summary to owner” | ❌ | PMS night audit + finance close |
| Laundry poundage | “Send 80 kg to laundry — track return” | 🟡 | Could file MAINTENANCE/OPERATIONS request; no weight tracking |
| Pool / spa capacity | “Pool at capacity — queue guests 15 min” | ❌ | Capacity sensor or manual counter |

---

## Part 4 — Retail & consumer goods

| Scenario | Example | Status | Gap |
|----------|---------|--------|-----|
| Stock transfer | “Move 20 units SKU-442 from warehouse to Store A” | ❌ | Multi-location inventory |
| Price label print | “Print shelf labels for promo starting Monday” | ❌ | Label printer integration |
| Shrinkage investigation | “Variance on aisle 3 — open investigation” | ❌ | CCTV timestamp link + inventory audit |
| Returns & refunds | “Customer return — receipt photo attached” | ❌ | RMA workflow + payment reversal |
| Loyalty points | “Add 500 points for Mrs. Alami” | ❌ | Loyalty platform API |
| Pharmacy expiry | “List products expiring in 30 days” | ❌ | Batch/expiry tracking (regulated) |
| Click & collect | “Order #882 ready for pickup — notify customer” | 🟡 | Guest order capture exists; limited notify path |

---

## Part 5 — Health, wellness & personal services

| Scenario | Example | Status | Gap |
|----------|---------|--------|-----|
| Appointment booking | “Book Dr. Hassan Tuesday 15:00 for Ahmed” | ❌ | Calendar + practitioner schedule + reminders |
| Consent forms | “Send pre-visit consent to patient” | ❌ | WhatsApp Flow + e-signature |
| Gym class roster | “Who’s registered for HIIT at 18:00?” | ❌ | Class booking system |
| Spa therapist load | “Sara has 6 massages — block lunch” | ❌ | Resource scheduling |
| Salon walk-in queue | “3 waiting — estimate 25 min” | ❌ | Queue display + SMS/WhatsApp updates |
| Medical cert for staff | “Generate sick note template for Karim” | 🟡 | Document request → HR lane; no auto-generation |

---

## Part 6 — Professional services & offices

| Scenario | Example | Status | Gap |
|----------|---------|--------|-----|
| Billable hours | “Log 2.5h on Client X — discovery call” | ❌ | Time tracking + invoicing |
| Proposal deadline | “Remind team: proposal due Friday — Acme tender” | ✅ | Dashboard task + calendar |
| Client onboarding pack | “Send onboarding docs to new client B” | 🟡 | Document send via WhatsApp; no CRM pipeline |
| Expense approval | “Approve Ahmed’s taxi 85 MAD — receipt attached” | 🟡 | Flow template defined but often `NOT_CONFIGURED` |
| Contract renewal | “Alert 90 days before lease ends” | ❌ | Contract repository + proactive intel |
| NDAs & access | “Grant temp access to auditor until 30 June” | ❌ | RBAC + audit trail |

---

## Part 7 — Logistics, field ops & maintenance contractors

| Scenario | Example | Status | Gap |
|----------|---------|--------|-----|
| Route dispatch | “Assign Route 7 to driver Youssef — 14 stops” | ❌ | Fleet management |
| Proof of delivery | “Photo + signature at drop-off” | ❌ | Mobile capture linked to order |
| Vehicle inspection | “Pre-trip checklist for van 12” | 🟡 | Checklist engine exists; no vehicle entity |
| Parts reorder | “Order brake pads when stock < 2” | ❌ | Auto-reorder rules |
| SLA breach | “Client ticket open 48h — escalate” | 🟡 | Task follow-ups exist; no external SLA clock |
| Subcontractor assign | “Send plumber to Riad Blue — gate code 4421” | 🟡 | Maintenance request + inform_staff; no vendor portal |

---

## Part 8 — Education, training & academies

| Scenario | Example | Status | Gap |
|----------|---------|--------|-----|
| Class attendance | “Mark absent: list for Module 3 today” | 🟡 | Attendance for *staff* exists; not student roster |
| Exam schedule | “Publish exam dates to cohort WhatsApp group” | 🟡 | Announcement yes; no academic calendar |
| Parent notification | “Notify parents — school closed tomorrow (rain)” | 🟡 | Broadcast possible; no parent directory model |
| Certification expiry | “List students whose food safety cert expires this month” | 🟡 | Staff doc expiry yes; not student certs |
| Training completion | “Who hasn’t finished harassment training?” | ❌ | LMS integration |

---

## Part 9 — Agriculture, production & manufacturing

| Scenario | Example | Status | Gap |
|----------|---------|--------|-----|
| Harvest log | “Record 1.2 tons olives — field Block C” | ❌ | Production batch tracking |
| Cold chain alert | “Fridge B hit 8°C — alert now” | 🔮 | IoT sensor → incident/maintenance |
| Batch traceability | “Trace lot #882 to supplier delivery date” | ❌ | Lot genealogy |
| Equipment downtime | “Press down 2h — log OEE impact” | 🟡 | Maintenance ticket; no OEE analytics |
| Seasonal hiring | “Need 20 pickers for 3 weeks — publish shift pool” | 🟡 | Shift templates; no gig pool marketplace |

---

## Part 10 — Events, entertainment & venues

| Scenario | Example | Status | Gap |
|----------|---------|--------|-----|
| Rider / tech spec | “Load rider for tonight’s band — stage plot attached” | ❌ | Event asset library |
| Ticket scan sync | “Doors open — how many entered?” | ❌ | Ticketing API |
| Artist settlement | “Pay DJ 15,000 MAD after set — contract attached” | 🟡 | Invoice record yes; no contract linkage |
| Merch stock | “Merch table low on XL — restock from trailer” | ❌ | Retail inventory at venue |
| Evacuation drill | “Start fire drill — log participation by zone” | ❌ | Safety drill module |

---

## Part 11 — Multi-site, franchise & portfolio

| Scenario | Example | Status | Gap |
|----------|---------|--------|-----|
| Cross-location KPI | “Compare labor % all sites last week” | 🟡 | `cross_location_report` exists; depth varies |
| Roll out SOP | “Push new opening checklist to all Morocco sites” | ❌ | Versioned SOP distribution + ack tracking |
| Franchise audit score | “Site Casablanca scored 78 — action plan” | ❌ | Audit template + CAPA workflow |
| Brand standards photo audit | “Score cleanliness from these 6 photos” | 🔮 | Vision model + scoring rubric |
| Central purchasing | “Negotiate group rate — all sites order via hub” | ❌ | Consolidated procurement |
| Owner daily digest | “WhatsApp me at 22:00: sales, labor, incidents, cash” | 🟡 | Scheduled intel partial; not full owner pack |

---

## Part 12 — By persona — what each role will ask

### 12.1 Owner / GM

| Request | Status | Notes |
|---------|--------|-------|
| “Are we going to hit budget this month?” | ❌ | FP&A, budget vs actual |
| “Who’s our most profitable daypart?” | 🟡 | POS analysis partial |
| “Approve all overtime over 10h this week” | ❌ | Bulk approval queue |
| “Open a second location in Rabat — project plan” | ❌ | Project management agent |
| “Sell the business — export 3-year P&L pack” | ❌ | Data export + anonymization |
| “What did Miya do for us this month?” | 🟡 | Activity log exists; no exec summary |

### 12.2 Floor / shift manager

| Request | Status | Notes |
|---------|--------|-------|
| “Call in backup — we’re slammed” | 🟡 | inform_staff + coverage tools partial |
| “Split section 4 between two servers” | ❌ | Floor plan / section model |
| “86 the special and tell floor” | ❌ | See menu availability |
| “Log walk-out — party of 6 angry” | 🟡 | Incident or ops request; no CRM |
| “Who hasn’t clocked in for 6pm shift?” | 🟡 | Attendance reports; proactive nudge partial |

### 12.3 Kitchen lead / chef

| Request | Status | Notes |
|---------|--------|-------|
| “Prep list for 180 covers tomorrow” | 🟡 | POS prep list tool; catering scale weak |
| “Substitute monkfish — use sea bream same weight” | ❌ | Recipe substitution engine |
| “Temperature log walk-in — 4°C” | ✅ | HACCP digital log · `temperature-log` agent API |
| “Fire table 12 — two mains, one allergy” | ❌ | KDS / table integration |
| “Order fish for Friday — best price from three suppliers” | ❌ | Multi-supplier RFQ |

### 12.4 Front-line staff

| Request | Status | Notes |
|---------|--------|-------|
| “Swap my Thursday for Friday” | ✅ | Shift swap flow |
| “I’m sick — can’t come in” | 🟡 | Leave flow / staff request |
| “When do I get paid?” | 🟡 | Payslip PDF generate ✅; self-service balance ❌ |
| “Upload my food handler certificate” | ✅ | Staff documents |
| “Translate this message to Darija for the guest” | 🔮 | Real-time translation in reply |
| “How much holiday do I have left?” | ❌ | Leave balance API |

### 12.5 HR / finance back office

| Request | Status | Notes |
|---------|--------|-------|
| “Generate payslips for March — all staff” | ✅ | Payroll engine · clock hours + rates · PDF |
| “Declare new hire to CNSS” | ❌ | Government portal integration |
| “Reconcile bank statement with invoices paid” | ❌ | Bank feed + matching |
| “Dunning letter for overdue client invoice” | ❌ | AR collections workflow |
| “Track probation end dates — alert 15 days before” | 🟡 | Doc/expiry patterns; no probation entity |

### 12.6 Suppliers & vendors

| Request | Status | Notes |
|---------|--------|-------|
| Supplier receives PO on WhatsApp — confirms delivery window | 🟡 | PO send exists; two-way confirm weak |
| “Invoice attached — match to PO #442” | 🟡 | record_invoice; PO matching ❌ |
| “Price list updated — apply from 1 July” | ❌ | Supplier catalog sync |
| Vendor portal: update delivery status | ❌ | External supplier app / Flow |

### 12.7 Guests & customers (guest-facing Miya)

| Request | Status | Notes |
|---------|--------|-------|
| “Table for 4 tonight 20:30” | 🟡 | Reservations list/create limited |
| “Where’s my delivery order?” | ❌ | Delivery tracker |
| “Allergic to shellfish — recommend dishes” | 🟡 | KB only |
| “Pay the bill split three ways” | ❌ | Payment split at table |
| “Leave a review — 5 stars for Sara” | ❌ | Review routing to recognition |
| Guest WhatsApp → staff without exposing personal numbers | 🔮 | Relay channel / masked messaging |

### 12.8 Auditors, regulators & insurers

| Request | Status | Notes |
|---------|--------|-------|
| “Export all incident reports Q1 with photos” | 🟡 | List/export partial |
| “Show clock-in records for employee X — March” | 🟡 | Attendance export |
| “Prove training completed for all food handlers” | 🟡 | Document list; no compliance dashboard |
| Read-only auditor access for 14 days | ❌ | Time-bound RBAC |

---

## Part 13 — Cross-cutting capabilities (the real product gaps)

These unlock **many** vertical scenarios at once.

### 13.1 Money movement

| Capability | Enables | Status |
|------------|---------|--------|
| Pay supplier from WhatsApp (bank transfer / check tracking) | Baker payment end-to-end | 🟡 Record invoice ✅ · bank payment status ✅ · initiate transfer ❌ |
| Staff expense reimbursement | Taxi, supplies | 🟡 Flow often unconfigured |
| Customer payment links | Deposits, catering | ❌ Stripe exists in backend; not Miya-facing |
| Cash variance root cause | “Why is drawer short 120 MAD?” | 🟡 Cash recon open/close only |
| Multi-currency & FX | Tunisia + Morocco portfolio | ❌ |

### 13.2 Documents & signatures

| Capability | Status |
|------------|--------|
| E-sign employment contract | ❌ |
| Parse any invoice PDF → auto record | 🟡 parse_document + confirm |
| Generate PDF payslip / attestation | 🟡 Staff PDF report partial |
| Version-controlled SOP library with “staff acknowledged” | ❌ |

### 13.3 Scheduling intelligence

| Capability | Status |
|------------|--------|
| “Schedule next week to hit 22% labor target” | 🟡 optimize exists; goal-seek weak |
| Auto-fill call-ins from bench pool | ❌ |
| Fairness rules (“no clopen twice in a row”) | 🟡 Partial in conflict engine |
| Weather-aware staffing | 🔮 Forecast + demand model |

### 13.4 Communications beyond broadcast

| Capability | Status |
|------------|--------|
| WhatsApp **groups** as first-class targets | ❌ |
| Threaded conversation per task/request | 🟡 Task exists; no guest-visible thread |
| Auto-translate outbound to staff preferred language | ❌ |
| Voice note → structured action (already good) | ✅ Preprocessor + Whisper path |
| Email + SMS fallback when WhatsApp fails | ❌ |

### 13.5 Integrations marketplace

| Integration | Status |
|-------------|--------|
| Square / Toast / Clover / Custom POS | 🟡 |
| Google Calendar | 🟡 OAuth required |
| Delivery: Glovo, Uber Eats, Deliveroo | 🟡 Glovo menu snapshot export ✅ · live API ❌ |
| Accounting: Sage, QuickBooks, Odoo | ❌ |
| Payroll: local Morocco providers | ❌ |
| PMS: Opera, Mews, Cloudbeds | ❌ |
| BI: Looker, Metabase embed | ❌ |
| IoT: fridge sensors, door locks | ❌ |

### 13.6 Trust, proof & anti-hallucination

| Capability | Status |
|------------|--------|
| Every action returns **record_id** / ref | ✅ Design rule; enforcement uneven |
| “Show me proof” → deep link to dashboard record | 🟡 |
| Undo / cancel last action | ❌ |
| Full audit trail export | 🟡 Activity log |
| Role-based “Miya cannot do that” | 🟡 Role grants partial |

### 13.7 Follow-ups, urgency & proactive chase (core loop)

| Proactive / chase behavior | App | WhatsApp | Status |
|----------------------------|-----|----------|--------|
| Auto follow-up on pending **dashboard tasks** | Widget + task status | Assignee nudges (24h window) | ✅ |
| Auto follow-up on pending **staff requests** (URGENT/HIGH) | SLA comments · urgent widget | Assignee chase | 🟡 App only today |
| Escalate to manager when chase exhausted | urgent_top · bell | Manager WhatsApp | ❌ |
| “Invoice #882 overdue tomorrow — pay now?” | Finance widget | Manager / AP assignee | 🟡 |
| “No-show risk: 3 staff haven’t confirmed tomorrow” | Scheduling view | `inform_staff` possible | 🟡 |
| “Supplier hasn’t confirmed PO — chase” | Purchase widget | Supplier + internal assignee | 🟡 |
| “Certificate expiring — block shift assignment?” | Compliance lane | Staff + manager | 🟡 Partial |
| Daily owner briefing — **including open urgent count** | Dashboard digest | WhatsApp summary | 🟡 |
| “Sales spike — suggest calling in backup” | Intel insight | `inform_staff` | 🔮 |

---

## Part 14 — Example conversations we must nail (acceptance tests)

Use these as **QA scripts** when judging “Miya handles it like a pro.”

### Finance

```
Manager: We need to pay the baker
Miya:    [asks only if missing] amount, invoice #, due date, method
Manager: 4000 mad facture 878789 échéance 30 juillet par chèque
Miya:    ✓ Recorded invoice #878789 — Boulanger 4,000 MAD due 30 Jul. Ref: INV-xxx
```

### HR / operations

```
Manager: Need to prepare payslips for the staff
Manager: Just a daily reminder in HR
Miya:    ✓ Saved daily reminder on Payroll lane. Task ref: TSK-xxx
```

### Facilities

```
Manager: Il faut réparer les wc hommes
Miya:    ✓ Logged maintenance — Men's restroom repair. Ref: REQ-xxx. [Assignee] notified.
```

### Stretch goals (not yet)

```
Owner:   If labor goes over 26% tonight, text me and pause hiring suggestions until I approve
Miya:    [monitor POS + clock data] → alert at 26.1% with one-tap approve/dismiss

Chef:    Build prep for 200 covers Saturday using last week's wedding + current stock
Miya:    [BOM + inventory + sales forecast] → prep list PDF + purchase gaps

Guest:   Table for 2, terrace, 21:00, nut allergy
Miya:    [reservation + kitchen flag + confirmation Flow to guest]
```

---

## Part 15 — Agent & architecture implications

To cover this vision without one monolithic prompt:

| New or expanded agent | Responsibility |
|----------------------|----------------|
| **miya-payroll** (future) | Payslips, statutory filings, leave balances |
| **miya-procurement** (future) | RFQ, PO matching, supplier catalog, three-way match |
| **miya-guest** (future) | Guest-facing concierge, reservations, delivery status |
| **miya-compliance** (future) | Audits, training ack, regulatory exports |
| **miya-projects** (future) | Openings, renovations, cross-functional timelines |
| Expanded **miya-intel** | Simulation, “what-if”, portfolio copilot |
| Expanded **operations preprocessor** | More deterministic intents (comp, 86, transfer stock) |

**Design principles (carry forward):**

1. **Deterministic first** — high-frequency ops intents via preprocessor, not LLM hope.
2. **One question max** — then execute with defaults.
3. **Proof in every success message** — ref ID, assignee, next step.
4. **Close the loop** — create → WhatsApp notify → app visibility → auto follow-up → escalate; never “I’ll keep it in mind” without a saved record and chase plan.
5. **Dual channel** — every pending/urgent item lives in the **app** and is actionable on **WhatsApp** for staff.
6. **Vertical packs** — hotel pack, retail pack, clinic pack = config + tools + persona overlays, not forked codebases.
7. **Fail loud, not vague** — never “problème technique” without tool attempt + retry path.

---

## Part 16 — Suggested roadmap tiers

### P0 — Harden the wedge (restaurant GM on WhatsApp)

- [x] All operational preprocessors deployed on every specialist *(deploy required)*
- [x] **Follow-up defaults on** — `follow_up_enabled=true` for maintenance / PO staff requests
- [x] **Staff-request WhatsApp follow-ups** — `staff_request_follow_up_sweep`
- [x] **Manager escalation** — tasks + staff requests when follow-ups exhausted
- [ ] Celery Beat running in production (`task_follow_up_sweep`, `staff_request_follow_up_sweep`, `staff_request_sla_sweep`)
- [ ] Expense claim + shift swap Flows configured in production
- [ ] Google Calendar connect UX stable
- [ ] PO ↔ invoice matching (light)
- [ ] Leave balance read API
- [ ] Proof links in every success reply · relay `whatsapp_sent` + follow-up count honestly

### P1 — Money & compliance depth (Morocco)

- [x] Payslip generation (PDF) from recorded hours + rates
- [x] CNSS / tax calendar reminders (not filing yet)
- [x] Bank payment status on recorded invoices
- [x] HACCP temperature log via WhatsApp
- [x] Delivery app menu sync (one provider)

### P2 — Multi-site & guest

- [ ] Cross-location inventory transfer
- [ ] Guest reservation Flow + allergy flags
- [ ] Franchise SOP push + acknowledgment
- [ ] Supplier two-way WhatsApp confirm
- [ ] Owner nightly digest (configurable)

### P3 — North star

- [ ] Guest-facing Miya on tenant WhatsApp number
- [ ] IoT-triggered maintenance + compliance
- [ ] Goal-seeking scheduler (“hit 22% labor”)
- [ ] Vision-based audit scoring
- [ ] Industry packs: hotel, pharmacy, gym, field service
- [ ] Marketplace integrations (accounting, PMS, payroll)

---

## Part 17 — Contributing to this doc

When a customer asks for something Miya can’t do:

1. Add a row under the closest vertical (or create a new subsection).
2. Tag ✅ / 🟡 / ❌ / 🔮 honestly.
3. Note which **agent**, **tool**, or **integration** would own it.
4. Link Linear/Jira ticket if scoped.

**File:** `mizan-backend/docs/MIYA_SCENARIO_VISION.md`

---

*This document is intentionally expansive. The product wins by making the restaurant / hospitality wedge feel magical first — then reusing the same primitives (requests, tasks, invoices, shifts, messages, knowledge, **follow-ups**) as LEGO blocks for every other vertical. Miya’s job is not done when something is logged; it is done when pending and urgent work is moving — visible in the app and chased on WhatsApp.*
