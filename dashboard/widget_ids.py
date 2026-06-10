"""Canonical dashboard widget ids (keep in sync with mizan-frontend DASHBOARD_WIDGET_IDS)."""

# Miya-created widgets use tokens: custom:<uuid> (see DashboardCustomWidget model).
CUSTOM_WIDGET_PREFIX = "custom:"

DEFAULT_DASHBOARD_WIDGET_ORDER = [
    "insights",
    "tasks_demands",
    "staffing",
    "sales_or_tasks",
    "operations",
    "wellbeing",
]

DASHBOARD_WIDGET_IDS = frozenset(
    [
        *DEFAULT_DASHBOARD_WIDGET_ORDER,
        "live_attendance",
        "compliance_risk",
        "inventory_delivery",
        "task_execution",
        "take_orders",
        "reservations",
        "retail_store_ops",
        "jobsite_crew",
        "ops_reports",
        "staff_inbox",
        "meetings_reminders",
        "clock_ins",
        "incidents",
        # Category-bucketed widgets backed by /api/dashboard/category-tasks/
        "urgent_top",
        "human_resources",
        "finance",
        "maintenance",
        "operations_tasks",
        "purchase_orders",
        "miscellaneous",
        "staff_messages",
    ]
)

# Natural-language / typo ids the agent may send instead of snake_case.
_AGENT_WIDGET_SYNONYMS: dict[str, str] = {
    "attendance": "clock_ins",
    "attendances": "clock_ins",
    "clock_in": "clock_ins",
    "clockin": "clock_ins",
    "clockins": "clock_ins",
    "clocking": "clock_ins",
    "pointage": "clock_ins",
    "pointages": "clock_ins",
    "liveattendance": "live_attendance",
    "live_attendance": "live_attendance",
}


def normalize_agent_widget_id(raw: str | None) -> str:
    """Map common LLM/user synonyms to a canonical id from ``DASHBOARD_WIDGET_IDS``."""
    if not isinstance(raw, str):
        return ""
    st = raw.strip()
    if not st:
        return ""
    if st.lower().startswith("custom:"):
        return st
    key = st.lower().replace("-", "_")
    key = "_".join(p for p in key.replace(" ", "_").split("_") if p)
    if not key:
        return ""
    return _AGENT_WIDGET_SYNONYMS.get(key, key)


# Icons allowed for Miya-created dashboard tiles (frontend maps to Lucide).
ALLOWED_CUSTOM_WIDGET_ICONS = frozenset(
    [
        "sparkles",
        "clipboard-check",
        "list-todo",
        "calendar",
        "users",
        "package",
        "shopping-cart",
        "file-text",
        "bar-chart-2",
        "clipboard-list",
        "hard-hat",
        "store",
        "inbox",
        "activity",
        "shield-alert",
        "clock",
        "heart",
        "calendar-days",
        "layout-grid",
    ]
)
