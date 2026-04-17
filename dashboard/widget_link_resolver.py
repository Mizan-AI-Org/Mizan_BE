"""
Resolve a custom dashboard widget's `link_url` from its human title.

Managers should not have to know or type frontend routes when creating a
dashboard shortcut — this module takes a free-form title (e.g. "Supplier
contacts", "Weekly safety walkthrough") and maps it to a real in-app route
using a curated keyword table.

Strategy: simple keyword scoring. Each entry is (keywords, route). For every
keyword present in the lowercased title we add 1 to that entry's score; the
highest-scoring route wins. Ties are broken by declaration order.

Kept deliberately small and curated — we can grow this table over time, and
the Miya agent can override it by passing an explicit `link_url`.
"""

from __future__ import annotations

import re

# Order matters as a tie-breaker. Put more specific mappings BEFORE generic ones.
#
# Keywords are compared via case-insensitive substring match, so "chats"
# naturally matches "chat". Multi-word keywords require the full phrase.
_KEYWORD_MAP: list[tuple[tuple[str, ...], str]] = [
    # === Supplier / inventory / purchasing ===
    (("supplier", "suppliers", "vendor", "vendors", "fournisseur"), "/dashboard/inventory/suppliers"),
    (("purchase order", "purchase orders", " po ", "procurement", "bon de commande"), "/dashboard/inventory/purchase-orders"),
    (("stock adjustment", "stock count", "stock take", "cycle count", "inventory count"), "/dashboard/inventory/stock-adjustments"),
    (("inventory", "stock", "ingredient", "ingredients"), "/dashboard/inventory/items"),

    # === Sales / reports / analytics ===
    (("daily sales", "sales report", "revenue report"), "/dashboard/reports/sales/daily"),
    (("attendance report",), "/dashboard/reports/attendance"),
    (("inventory report", "stock report"), "/dashboard/reports/inventory"),
    (("labor report", "labour report", "labor attendance", "labour attendance", "payroll"), "/dashboard/reports/labor-attendance"),
    (("kpi", "metrics", "dashboard metric", "analytics", "insight", "insights", "report", "reports"), "/dashboard/reports"),
    (("sales", "revenue", "pos", "takings"), "/dashboard/sales-analysis"),

    # === Scheduling / shifts ===
    (("schedule analytics", "scheduling analytics", "shift analytics"), "/dashboard/scheduling/analytics"),
    (("swap request", "shift swap"), "/dashboard/swap-requests"),
    (("emergency availability", "emergency cover", "last minute cover"), "/dashboard/emergency-availability"),
    (("schedule", "roster", "rota", "shift", "shifts", "scheduling", "planning", "timetable"), "/dashboard/scheduling"),

    # === Staff / HR ===
    (("staff request", "time off", "time-off", "leave request", "holiday request", "absence", "leave"), "/dashboard/staff-requests"),
    (("staff management", "team management", "hr", "human resource", "employee management", "people"), "/dashboard/staff-management"),
    (("staff", "team", "employees", "crew", "workforce"), "/dashboard/staff-app"),
    (("attendance", "clock in", "clock out", "time and attendance", "timesheet"), "/dashboard/attendance"),

    # === Tasks & checklists ===
    (("task template", "task templates"), "/dashboard/task-templates"),
    (("checklist template", "checklist templates"), "/dashboard/checklists/templates"),
    (("review", "manager review", "review checklist", "checklist review"), "/dashboard/reviews/checklists"),
    (("checklist", "checklists", "sop", "procedure"), "/dashboard/checklists/templates"),
    (("task", "tasks", "to do", "todo", "process", "processes"), "/dashboard/processes-tasks-app"),

    # === Communication ===
    (("announcement", "announcements", "broadcast", "bulletin", "news", "notice"), "/dashboard/staff-chat"),
    (("chat", "chats", "message", "messages", "messaging", "inbox", "conversation", "factory chat", "site chat", "team chat"), "/dashboard/staff-chat"),

    # === Front-of-house / F&B ===
    (("reservation", "reservations", "booking", "bookings", "guest list"), "/dashboard/reservations"),
    (("table management", "tables", "floor plan", "seating"), "/dashboard/table-management"),
    (("take order", "order taking", "capture order", "captured order"), "/dashboard/take-orders"),
    (("orders", "open orders", "order"), "/dashboard/take-orders"),
    (("prep list", "prep", "mise en place", "sales and prep", "sales & prep"), "/dashboard/sales-and-prep"),
    (("menu", "menu management", "recipe", "recipes"), "/dashboard/menu"),

    # === Kitchen / back-of-house ===
    (("kitchen display", "kds", "kitchen"), "/dashboard/kitchen"),
    (("waste", "food waste", "spoilage"), "/dashboard/inventory/waste"),

    # === Safety & compliance ===
    (("incident", "incidents", "accident", "near miss"), "/dashboard/incidents"),
    (("safety walk", "safety walkthrough", "safety check"), "/dashboard/checklists/templates"),
    (("safety", "haccp", "food safety", "compliance", "audit"), "/dashboard/safety"),

    # === Ops / supervisor ===
    (("supervisor",), "/dashboard/supervisor"),
    (("action center", "action centre", "action"), "/dashboard/action-center"),
    (("alert", "alerts", "priority"), "/dashboard/alerts"),

    # === Settings / admin ===
    (("permission", "role permission", "rbac", "access control"), "/dashboard/settings/permissions"),
    (("setting", "settings", "config", "configuration", "preferences"), "/dashboard/settings"),
    (("profile", "account", "my profile"), "/dashboard/profile"),
    (("billing", "subscription", "invoice", "invoices", "plan"), "/dashboard/settings"),

    # === Home / overview ===
    (("home", "dashboard", "overview"), "/dashboard"),
]

_URL_RE = re.compile(r"^(https?://|/)")


def looks_like_url(value: str | None) -> bool:
    """True if the given string already looks like a URL/path we should accept as-is."""
    if not value:
        return False
    return bool(_URL_RE.match(value.strip()))


def resolve_link_from_title(title: str | None) -> str:
    """
    Return a best-guess in-app route for the given title, or "" if nothing
    matches with confidence.
    """
    if not title:
        return ""
    lowered = title.lower()
    best_route = ""
    best_score = 0
    for keywords, route in _KEYWORD_MAP:
        score = sum(1 for kw in keywords if kw in lowered)
        if score > best_score:
            best_score = score
            best_route = route
    return best_route


def ensure_link(title: str | None, provided: str | None) -> str:
    """
    If the caller supplied a URL/path we trust it; otherwise derive one from
    the title. Return "" when nothing can be resolved.
    """
    if looks_like_url(provided):
        return (provided or "").strip()[:2048]
    return resolve_link_from_title(title)
