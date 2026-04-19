"""
Static RBAC catalog for tenant role-based permissions.

Three orthogonal capability namespaces:
- APPS      — top-level product areas / routes a user can open.
- WIDGETS   — built-in dashboard widget ids (see mizan-frontend/.../DashboardWidgets.tsx
              and mizan-backend/dashboard/widget_ids.py).
- ACTIONS   — individual write operations that callers may want to gate
              (approve/time-off, edit schedule, delete staff, etc).

SUPER_ADMIN, ADMIN and OWNER always bypass these checks (full access).
MANAGER and every other staff role resolve through RolePermissionSet or the
DEFAULT_PERMISSIONS below when no row exists for (restaurant, role).

Lists here are the single source of truth shown in the admin UI; adding a new
id requires updating only this file + whatever surface consumes the id.
"""

from __future__ import annotations

APPS: list[dict[str, str]] = [
    {"id": "tasks", "label": "Processes & tasks"},
    {"id": "staff", "label": "Staff"},
    {"id": "checklists", "label": "Checklists & incidents"},
    {"id": "shift_reviews", "label": "Staff schedules"},
    {"id": "scheduling", "label": "Scheduling"},
    {"id": "scheduling_analytics", "label": "Scheduling analytics"},
    {"id": "reports", "label": "Reports"},
    {"id": "reports_sales", "label": "Sales reports"},
    {"id": "reports_attendance", "label": "Attendance reports"},
    {"id": "reports_inventory", "label": "Inventory reports"},
    {"id": "reports_labor", "label": "Labor reports"},
    {"id": "attendance", "label": "Attendance"},
    {"id": "reservations", "label": "Reservations"},
    {"id": "sales_and_prep", "label": "Sales & prep"},
    {"id": "swap_requests", "label": "Swap requests"},
    {"id": "staff_management", "label": "Staff management"},
    {"id": "staff_requests", "label": "Staff inbox / requests"},
    {"id": "table_management", "label": "Table management"},
    {"id": "supervisor", "label": "Supervisor dashboard"},
    {"id": "locations_overview", "label": "Locations overview (multi-branch command center)"},
    {"id": "inventory", "label": "Inventory"},
    {"id": "take_orders", "label": "Take orders"},
    {"id": "settings", "label": "Settings"},
    {"id": "rbac", "label": "Role permissions (admin only)"},
]

# Keep in sync with dashboard.widget_ids.DASHBOARD_WIDGET_IDS.
WIDGETS: list[dict[str, str]] = [
    {"id": "insights", "label": "Insights"},
    {"id": "staffing", "label": "Staffing coverage"},
    {"id": "sales_or_tasks", "label": "Sales / today's tasks"},
    {"id": "operations", "label": "Operations quality"},
    {"id": "wellbeing", "label": "Team wellbeing"},
    {"id": "live_attendance", "label": "Live attendance"},
    {"id": "compliance_risk", "label": "Compliance & risk"},
    {"id": "inventory_delivery", "label": "Inventory / deliveries"},
    {"id": "task_execution", "label": "Task execution"},
    {"id": "take_orders", "label": "Take orders"},
    {"id": "reservations", "label": "Reservations"},
    {"id": "retail_store_ops", "label": "Retail store ops"},
    {"id": "jobsite_crew", "label": "Jobsite / crew"},
    {"id": "ops_reports", "label": "Ops reports"},
    {"id": "staff_inbox", "label": "Staff inbox"},
]

ACTIONS: list[dict[str, str]] = [
    {"id": "approve_time_off", "label": "Approve / deny time-off requests"},
    {"id": "approve_swap", "label": "Approve / deny shift swap requests"},
    {"id": "edit_schedule", "label": "Create & edit shifts"},
    {"id": "publish_schedule", "label": "Publish schedule"},
    {"id": "invite_staff", "label": "Invite new staff"},
    {"id": "edit_staff", "label": "Edit staff profile / role"},
    {"id": "delete_staff", "label": "Deactivate / delete staff"},
    {"id": "view_payroll", "label": "View payroll-sensitive data"},
    {"id": "edit_inventory", "label": "Edit inventory items / POs"},
    {"id": "run_reports", "label": "Run & export reports"},
    {"id": "manage_settings", "label": "Change workspace settings"},
    {"id": "manage_integrations", "label": "Connect / edit integrations (POS, reservations, SMS)"},
    {"id": "create_widget_category", "label": "Create dashboard widget categories"},
    {"id": "manage_widgets", "label": "Create / edit dashboard shortcuts"},
    {"id": "miya_full_tools", "label": "Use all Miya / Lua agent tools"},
]


APP_IDS: set[str] = {a["id"] for a in APPS}
WIDGET_IDS: set[str] = {w["id"] for w in WIDGETS}
ACTION_IDS: set[str] = {a["id"] for a in ACTIONS}


# Roles the SUPER_ADMIN / OWNER can configure. Admin-tier roles are never
# editable through the UI (they always have full access on the backend).
EDITABLE_ROLES: list[str] = [
    "MANAGER",
    "SUPERVISOR",
    "CHEF",
    "WAITER",
    "CASHIER",
    "KITCHEN_STAFF",
    "CLEANER",
    "DELIVERY",
    "CUSTOM",
]

# Roles that always receive full permissions (never gated).
PRIVILEGED_ROLES: set[str] = {"SUPER_ADMIN", "ADMIN", "OWNER"}


def _ids(entries: list[dict[str, str]]) -> list[str]:
    return [e["id"] for e in entries]


# Sensible defaults applied when no RolePermissionSet row exists for (tenant, role).
# MANAGER keeps today's "can do most things except admin settings & RBAC".
DEFAULT_PERMISSIONS: dict[str, dict[str, list[str]]] = {
    "MANAGER": {
        "apps": [
            a for a in _ids(APPS)
            if a not in ("settings", "rbac", "reports_sales", "reports_inventory")
        ],
        "widgets": _ids(WIDGETS),
        "actions": [
            "approve_time_off",
            "approve_swap",
            "edit_schedule",
            "publish_schedule",
            "invite_staff",
            "edit_staff",
            "edit_inventory",
            "run_reports",
            "create_widget_category",
            "manage_widgets",
            "miya_full_tools",
        ],
    },
    "SUPERVISOR": {
        "apps": ["staff", "shift_reviews", "attendance", "scheduling", "supervisor", "staff_requests"],
        "widgets": ["insights", "staffing", "operations", "wellbeing", "live_attendance", "staff_inbox"],
        "actions": ["edit_schedule"],
    },
    "CHEF": {
        "apps": ["scheduling", "inventory", "shift_reviews"],
        "widgets": ["insights", "operations", "inventory_delivery", "task_execution"],
        "actions": [],
    },
    "WAITER": {
        "apps": ["take_orders", "reservations"],
        "widgets": ["take_orders", "reservations", "live_attendance"],
        "actions": [],
    },
    "CASHIER": {
        "apps": ["take_orders"],
        "widgets": ["take_orders"],
        "actions": [],
    },
    "KITCHEN_STAFF": {
        "apps": ["inventory"],
        "widgets": ["task_execution", "inventory_delivery"],
        "actions": [],
    },
    "CLEANER": {"apps": [], "widgets": ["task_execution"], "actions": []},
    "DELIVERY": {"apps": [], "widgets": ["inventory_delivery"], "actions": []},
    "CUSTOM": {"apps": [], "widgets": [], "actions": []},
}


def default_permissions_for(role: str) -> dict[str, list[str]]:
    return {
        "apps": list(DEFAULT_PERMISSIONS.get(role, {}).get("apps", [])),
        "widgets": list(DEFAULT_PERMISSIONS.get(role, {}).get("widgets", [])),
        "actions": list(DEFAULT_PERMISSIONS.get(role, {}).get("actions", [])),
    }


def full_permissions() -> dict[str, list[str]]:
    return {
        "apps": list(APP_IDS),
        "widgets": list(WIDGET_IDS),
        "actions": list(ACTION_IDS),
    }


def sanitize_permissions(raw) -> dict[str, list[str]]:
    """Filter and dedupe incoming permission lists against the known catalog."""

    def clean(values, valid):
        if not isinstance(values, list):
            return []
        out: list[str] = []
        seen: set[str] = set()
        for v in values:
            if isinstance(v, str) and v in valid and v not in seen:
                seen.add(v)
                out.append(v)
        return out

    data = raw if isinstance(raw, dict) else {}
    return {
        "apps": clean(data.get("apps"), APP_IDS),
        "widgets": clean(data.get("widgets"), WIDGET_IDS),
        "actions": clean(data.get("actions"), ACTION_IDS),
    }
