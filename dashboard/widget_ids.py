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
        # Procurement asks ("buy 6 bottles of vodka") — lives behind
        # /api/dashboard/category-tasks/?bucket=purchase_orders so it
        # already shows real PURCHASE_ORDER staff requests + tasks.
        "purchase_orders",
        # Catch-all lane for general / uncategorised requests Miya couldn't
        # slot into a named category. Lives behind the same endpoint as the
        # named lanes (bucket=miscellaneous) and is allow-listed here so
        # /api/dashboard/widget-order/ doesn't strip it on PATCH.
        "miscellaneous",
        # Admin → Staff WhatsApp composer + delivery feed. Same
        # NotificationService Miya's `inform_staff` tool uses.
        "staff_messages",
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
