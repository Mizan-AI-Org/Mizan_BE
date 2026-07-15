"""
Platform-level knowledge for Miya (features, workflows, RBAC) — not tenant SOPs.

Searched by keyword for MVP; tenant operational docs stay in Lua knowledge_base.
"""
from __future__ import annotations

import re
from typing import Any

# Curated platform playbook entries (expand over time; keep short and actionable).
PLATFORM_KNOWLEDGE: list[dict[str, str]] = [
    {
        "id": "mgr-sales",
        "title": "Today's sales (manager)",
        "category": "feature",
        "audience": "manager",
        "content": (
            "Ask Miya: 'What are today's sales?' She loads live POS totals via sales tools. "
            "Never invent figures — if POS is disconnected she says so. Manager-only."
        ),
        "keywords": "sales today chiffre ventes revenue pos report",
    },
    {
        "id": "mgr-low-stock",
        "title": "Low stock / reorder",
        "category": "feature",
        "audience": "both",
        "content": (
            "Ask: 'What's running low?' Miya lists inventory at or below reorder level. "
            "Managers can follow with 'recommend today's purchases' for a reorder list."
        ),
        "keywords": "low stock running low reorder inventory purchases",
    },
    {
        "id": "mgr-food-cost",
        "title": "Recipe food cost & margin",
        "category": "feature",
        "audience": "manager",
        "content": (
            "Ask: 'What's our food cost?' or 'Which dishes have the worst margin?' "
            "Miya uses recipe BOM × ingredient costs vs menu price. Requires recipes with costs."
        ),
        "keywords": "food cost margin recipe bom portion cost profit dish",
    },
    {
        "id": "mgr-invoice-po",
        "title": "Match invoice to purchase order",
        "category": "workflow",
        "audience": "manager",
        "content": (
            "After recording an invoice, ask Miya to match it to a PO. She suggests suppliers "
            "with similar name and amount (±5%), then you confirm the link."
        ),
        "keywords": "invoice purchase order po match reconcile bill supplier",
    },
    {
        "id": "mgr-digest",
        "title": "Nightly manager ops digest",
        "category": "feature",
        "audience": "manager",
        "content": (
            "Managers with WhatsApp can receive an evening ops digest: no-shows, understaffing, "
            "open staff requests, overdue invoices. Toggle digest_enabled in notification preferences."
        ),
        "keywords": "digest briefing nightly proactive whatsapp summary",
    },
    {
        "id": "staff-next",
        "title": "What should I do next? (staff)",
        "category": "feature",
        "audience": "staff",
        "content": (
            "On WhatsApp say 'What should I do next?' Miya shows checklist / task preview for your shift. "
            "Also: clock in (share location), my shifts, start checklist, report incident."
        ),
        "keywords": "what next checklist tasks companion staff shift",
    },
    {
        "id": "staff-escalate",
        "title": "Tell my manager",
        "category": "workflow",
        "audience": "staff",
        "content": (
            "Say 'Tell my manager…' / report absence / early pay. Miya creates a StaffRequest "
            "in the manager inbox — she must not invent 'logged' without the tool succeeding."
        ),
        "keywords": "tell manager absence sick early pay escalate hr payroll",
    },
    {
        "id": "rbac",
        "title": "Manager vs staff permissions",
        "category": "policy",
        "audience": "both",
        "content": (
            "Managers (copilot): sales, supplier orders, invoices, grants, digests. "
            "Staff (companion): shifts, clock, checklists, incidents, tell-manager. "
            "Staff asking for manager-only tools get a polite redirect."
        ),
        "keywords": "permission role manager staff rbac access denied",
    },
    {
        "id": "knowledge-tenant",
        "title": "Restaurant procedures (SOPs)",
        "category": "feature",
        "audience": "both",
        "content": (
            "Tenant SOPs live in the knowledge_base tool (per restaurant). Managers can add "
            "procedures; staff search with 'how do I…'. Platform feature help uses platform_knowledge."
        ),
        "keywords": "sop procedure how do i knowledge base training policy",
    },
    {
        "id": "anti-hallucination",
        "title": "Never invent live data",
        "category": "policy",
        "audience": "both",
        "content": (
            "Miya must use tools/APIs for sales, stock, shifts, invoices. If a tool fails, "
            "say so honestly — never invent 'technical issue' success or fake numbers."
        ),
        "keywords": "hallucinate invent fake data technical issue tool",
    },
]


def search_platform_knowledge(query: str, *, limit: int = 5, audience: str | None = None) -> list[dict[str, Any]]:
    q = (query or "").strip().lower()
    if not q:
        return []
    tokens = [t for t in re.split(r"\W+", q) if len(t) >= 2]
    scored: list[tuple[float, dict[str, Any]]] = []
    for entry in PLATFORM_KNOWLEDGE:
        if audience in ("manager", "staff") and entry.get("audience") not in (audience, "both"):
            continue
        blob = f"{entry['title']} {entry['content']} {entry.get('keywords', '')}".lower()
        score = 0.0
        if q in blob:
            score += 3.0
        for t in tokens:
            if t in blob:
                score += 1.0
            if t in (entry.get("keywords") or "").lower():
                score += 0.5
        if score > 0:
            scored.append(
                (
                    score,
                    {
                        "id": entry["id"],
                        "title": entry["title"],
                        "category": entry["category"],
                        "audience": entry["audience"],
                        "content": entry["content"],
                        "relevance": round(min(score / (3 + len(tokens) or 1), 1.0), 2),
                    },
                )
            )
    scored.sort(key=lambda x: -x[0])
    return [e for _, e in scored[: max(1, min(int(limit or 5), 20))]]
