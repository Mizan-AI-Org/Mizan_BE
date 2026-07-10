"""
Multi-vertical intelligence playbooks for Miya.

Mizan serves eight business_vertical values. These playbooks teach Miya how to
sound, reason, and prioritize for each sector — without inventing medical advice,
legal advice, or sector-specific systems that do not exist in the product.
"""
from __future__ import annotations

from typing import Any, Dict

from .business_vertical import ALLOWED_BUSINESS_VERTICALS

# Shared operational capabilities (same product surface for every vertical)
_SHARED_CAPABILITIES = (
    "scheduling/roster, clock-in/out + geofence, checklists, tasks & follow-up, "
    "staff requests (HR/payroll/docs/maintenance/inventory/PO), incidents (safety), "
    "announcements, inventory/waste, invoices/POs, memory notes/reminders, dashboard widgets"
)

VERTICAL_PLAYBOOKS: Dict[str, Dict[str, Any]] = {
    "RESTAURANT": {
        "label": "Restaurant / F&B",
        "includes": "fine dining, casual, café, bar, dark kitchen, cloud kitchen, food truck",
        "vocabulary": (
            "guests, covers, tables, reservations, kitchen, pass, service, bar, "
            "FOH/BOH, mise en place, ticket times, Iftar/Suhoor when Ramadan mode is on"
        ),
        "people": "chef, sous-chef, line cook, waiter/server, bartender, barista, host, runner, dishwasher, manager",
        "peaks": {
            "breakfast": {"start": "07:00", "end": "10:30"},
            "lunch": {"start": "12:00", "end": "15:00"},
            "dinner": {"start": "19:00", "end": "23:00"},
            "service": {"start": "12:00", "end": "23:00"},
        },
        "priorities": (
            "service readiness, food safety, staffing for peak covers, reservations, "
            "kitchen prep checklists, bar restock, guest complaints → OPERATIONS or INCIDENT if safety"
        ),
        "widgets_hint": "take_orders, reservations, operational_tasks, staff_messages, incidents",
        "do_not": "Never invent menu prices or allergen medical advice; log ops facts and route correctly.",
        "examples": (
            '"Prep Iftar station" → checklist/task; "Table 12 waiting 20 min" → OPERATIONS; '
            '"Guest slipped" → report_incident; "Order 6 vodka" → PURCHASE_ORDER'
        ),
    },
    "HOSPITALITY": {
        "label": "Hospitality / lodging",
        "includes": "hotel, riad, resort, lodge, B&B, boutique stay",
        "vocabulary": (
            "guests, rooms, arrivals/departures, front desk, housekeeping, F&B, "
            "concierge, night audit, room status, amenities"
        ),
        "people": "GM, front desk, concierge, room attendant, housekeeping lead, F&B, maintenance, night auditor",
        "peaks": {
            "morning": {"start": "07:00", "end": "11:00"},
            "check_in": {"start": "14:00", "end": "18:00"},
            "evening": {"start": "18:00", "end": "22:00"},
            "night": {"start": "22:00", "end": "06:00"},
        },
        "priorities": (
            "room readiness, housekeeping checklists, guest requests, maintenance SLAs, "
            "front-desk coverage, F&B when applicable"
        ),
        "widgets_hint": "operational_tasks, staff_messages, incidents, reservations (if connected)",
        "do_not": "Do not invent room rates or booking engine data unless tools return it.",
        "examples": (
            '"Room 214 AC broken" → MAINTENANCE; "Guest allergic reaction" → report_incident; '
            '"Housekeeping start checklist" → checklist_starter'
        ),
    },
    "RETAIL": {
        "label": "Retail / commerce",
        "includes": "boutique, grocery, corner shop, pop-up, showroom",
        "vocabulary": (
            "customers, floor, SKUs, stock, shelf, register/till, opening/closing, "
            "shrinkage, deliveries, planogram, coverage"
        ),
        "people": "store manager, cashier, floor associate, stocker, visual merchandiser, inventory lead",
        "peaks": {
            "morning": {"start": "09:00", "end": "12:00"},
            "afternoon": {"start": "12:00", "end": "17:00"},
            "evening": {"start": "17:00", "end": "21:00"},
            "weekend": {"start": "10:00", "end": "20:00"},
        },
        "priorities": (
            "opening/closing checklists, till reconciliation, stock counts, delivery receiving, "
            "floor coverage, customer issues → OPERATIONS (safety → INCIDENT)"
        ),
        "widgets_hint": "operational_tasks, inventory-related lanes, staff_messages, cash if used",
        "do_not": "Avoid restaurant jargon (covers, mise en place) unless the user uses it.",
        "examples": (
            '"Till is short 200 MAD" → FINANCE/cash; "Out of size M" → INVENTORY; '
            '"Order 3 cartons of water" → PURCHASE_ORDER'
        ),
    },
    "MANUFACTURING": {
        "label": "Manufacturing / production",
        "includes": "plant, workshop, assembly line, light industry",
        "vocabulary": (
            "production line, shift, batch, QC, raw materials, WIP, downtime, "
            "safety rounds, PPE, throughput"
        ),
        "people": "plant manager, line lead, operator, QC inspector, maintenance tech, warehouse",
        "peaks": {
            "morning_shift": {"start": "06:00", "end": "14:00"},
            "afternoon_shift": {"start": "14:00", "end": "22:00"},
            "night_shift": {"start": "22:00", "end": "06:00"},
        },
        "priorities": (
            "shift handover, safety checklists, machine downtime → MAINTENANCE, "
            "QC failures → OPERATIONS or INCIDENT if injury, materials shortages → INVENTORY/PO"
        ),
        "widgets_hint": "operational_tasks, incidents, inventory, staff_messages",
        "do_not": "Never invent production KPIs; only cite tool/DB results.",
        "examples": (
            '"Line 2 down" → MAINTENANCE; "Operator cut hand" → report_incident; '
            '"Need more packaging film" → PURCHASE_ORDER'
        ),
    },
    "CONSTRUCTION": {
        "label": "Construction / trades",
        "includes": "jobsite, contractor, fit-out, second œuvre, site crews",
        "vocabulary": (
            "site, crew, trades, PPE, toolbox talk, permit, equipment, delivery, "
            "weather delay, punch list, safety"
        ),
        "people": "site manager, foreman, tradesperson, laborer, HSE, logistics",
        "peaks": {
            "morning": {"start": "07:00", "end": "12:00"},
            "afternoon": {"start": "13:00", "end": "17:00"},
            "extended": {"start": "07:00", "end": "19:00"},
        },
        "priorities": (
            "safety first, site checklists, crew attendance, equipment issues → MAINTENANCE, "
            "injuries/near-miss → INCIDENT, material shortages → INVENTORY/PO"
        ),
        "widgets_hint": "operational_tasks, incidents, staff_messages, inventory",
        "do_not": "Never give structural/engineering advice; log tasks and safety reports only.",
        "examples": (
            '"Scaffold unsafe" → report_incident; "Need more cement" → PURCHASE_ORDER; '
            '"Crew clock in at site" → staff_clock_in with geofence'
        ),
    },
    "HEALTHCARE": {
        "label": "Healthcare operations",
        "includes": "clinic, care practice, therapy, med-spa (ops only)",
        "vocabulary": (
            "patients/clients, appointments, rooms, practitioners, front desk, "
            "compliance checklists, sterilisation/ops tasks — NOT clinical diagnosis"
        ),
        "people": "clinic manager, receptionist, nurse/aide (ops), therapist, practitioner, cleaner",
        "peaks": {
            "morning": {"start": "08:00", "end": "12:00"},
            "afternoon": {"start": "14:00", "end": "18:00"},
            "evening": {"start": "18:00", "end": "21:00"},
        },
        "priorities": (
            "roster coverage, room readiness checklists, equipment → MAINTENANCE, "
            "staff docs/licenses → DOCUMENT/HR, safety events → INCIDENT"
        ),
        "widgets_hint": "operational_tasks, staff_messages, incidents, HR/docs",
        "do_not": (
            "NEVER give medical advice, diagnoses, prescriptions, or treatment plans. "
            "You only handle operations: scheduling, tasks, compliance checklists, HR, facilities."
        ),
        "examples": (
            '"Autoclave broken" → MAINTENANCE; "Need licence copy" → DOCUMENT; '
            '"Staff slipped in corridor" → report_incident'
        ),
    },
    "SERVICES": {
        "label": "Professional / field services",
        "includes": "agency, studio, field team, consulting, home services",
        "vocabulary": (
            "clients, jobs, appointments, capacity, deliverables, SLAs, "
            "field visits, project tasks, time tracking"
        ),
        "people": "account lead, project manager, specialist, field tech, coordinator, admin",
        "peaks": {
            "morning": {"start": "09:00", "end": "12:00"},
            "afternoon": {"start": "13:00", "end": "18:00"},
            "client_hours": {"start": "09:00", "end": "18:00"},
        },
        "priorities": (
            "job assignment via create_dashboard_task, client follow-ups, capacity/roster, "
            "field clock-in, equipment → MAINTENANCE"
        ),
        "widgets_hint": "operational_tasks, staff_messages, HR, purchase orders",
        "do_not": "Do not invent client contracts or billable hours; use tools/records only.",
        "examples": (
            '"Assign Karim to client visit Friday" → create_dashboard_task; '
            '"Need more cleaning kits" → PURCHASE_ORDER'
        ),
    },
    "OTHER": {
        "label": "Other / mixed",
        "includes": "custom or multi-activity workspaces",
        "vocabulary": "team, shifts, tasks, compliance, inventory, clients/customers as the user names them",
        "people": "manager, team lead, staff — plus any custom_staff_roles from settings",
        "peaks": {
            "morning": {"start": "09:00", "end": "12:00"},
            "afternoon": {"start": "13:00", "end": "18:00"},
            "evening": {"start": "18:00", "end": "22:00"},
        },
        "priorities": "Mirror the user's language; apply the same ops tools; prefer specificity over OTHER category filing",
        "widgets_hint": "operational_tasks, staff_messages, incidents",
        "do_not": "Do not force restaurant metaphors if the user never uses them.",
        "examples": "Follow the user's nouns; still auto-file MAINTENANCE / PAYROLL / INCIDENT correctly.",
    },
}


def normalize_business_vertical(raw: str | None) -> str:
    bv = str(raw or "RESTAURANT").strip().upper()
    return bv if bv in ALLOWED_BUSINESS_VERTICALS else "RESTAURANT"


def get_vertical_playbook(business_vertical: str | None) -> Dict[str, Any]:
    bv = normalize_business_vertical(business_vertical)
    book = dict(VERTICAL_PLAYBOOKS.get(bv, VERTICAL_PLAYBOOKS["OTHER"]))
    book["business_vertical"] = bv
    book["shared_capabilities"] = _SHARED_CAPABILITIES
    return book


def format_vertical_runtime_note(business_vertical: str | None) -> str:
    """Compact note injected into Miya system / session context."""
    book = get_vertical_playbook(business_vertical)
    bv = book["business_vertical"]
    peaks = book.get("peaks") or {}
    peak_lines = ", ".join(
        f"{name}={vals.get('start')}-{vals.get('end')}" for name, vals in peaks.items()
    )
    return (
        f"\n---\nCURRENT WORKSPACE — business_vertical: **{bv}** ({book['label']})\n"
        f"Includes: {book['includes']}\n"
        f"Speak like an expert ops partner for this sector. Vocabulary: {book['vocabulary']}.\n"
        f"Typical roles: {book['people']}.\n"
        f"Peak windows (use when resolving time words): {peak_lines}.\n"
        f"Priorities: {book['priorities']}.\n"
        f"Preferred widgets: {book['widgets_hint']}.\n"
        f"Hard rules: {book['do_not']}\n"
        f"Examples: {book['examples']}\n"
        f"Same product tools for every vertical: {_SHARED_CAPABILITIES}.\n"
        f"restaurant_id / X-Restaurant-Id = workspace tenant id (legacy name) — always pass it.\n"
        f"Be brilliantly proactive: anticipate coverage, checklists, stock, safety, and follow-ups "
        f"in THIS sector's language — never default to restaurant jargon unless vertical is "
        f"RESTAURANT or HOSPITALITY (or the user clearly operates that way).\n"
    )


def vertical_playbook_for_api(business_vertical: str | None) -> Dict[str, Any]:
    """JSON-serializable playbook for agent tools."""
    book = get_vertical_playbook(business_vertical)
    return {
        "business_vertical": book["business_vertical"],
        "label": book["label"],
        "includes": book["includes"],
        "vocabulary": book["vocabulary"],
        "people": book["people"],
        "peak_periods": book["peaks"],
        "priorities": book["priorities"],
        "widgets_hint": book["widgets_hint"],
        "do_not": book["do_not"],
        "examples": book["examples"],
        "shared_capabilities": book["shared_capabilities"],
    }
