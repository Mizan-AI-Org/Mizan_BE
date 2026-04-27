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
    ]
)

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
