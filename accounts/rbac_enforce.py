"""Resolve effective RBAC permissions for Miya tool gating."""

from __future__ import annotations

from typing import Any

from accounts.models import RolePermissionSet, UserPermissionSet
from accounts.rbac_catalog import (
    ACTIONS,
    APPS,
    APP_IDS,
    ACTION_IDS,
    DEFAULT_PERMISSIONS,
    PRIVILEGED_ROLES,
    WIDGET_IDS,
    default_permissions_for,
    full_permissions,
    sanitize_permissions,
)


def effective_permissions(user, restaurant=None) -> dict[str, list[str]]:
    """Return {apps, widgets, actions} for a user at an optional tenant."""
    role = (getattr(user, "role", "") or "").upper()
    if role in PRIVILEGED_ROLES:
        return full_permissions()

    if restaurant is None:
        restaurant = getattr(user, "restaurant", None)
    if restaurant is not None:
        from miya.services.tenant import effective_role_at_tenant

        role = effective_role_at_tenant(user, restaurant) or role
        if role in PRIVILEGED_ROLES:
            return full_permissions()

    if restaurant is not None:
        user_row = (
            UserPermissionSet.objects.filter(restaurant=restaurant, user=user)
            .only("permissions")
            .first()
        )
        if user_row is not None:
            return sanitize_permissions(user_row.permissions)

    if restaurant is not None:
        role_row = (
            RolePermissionSet.objects.filter(restaurant=restaurant, role=role)
            .only("permissions")
            .first()
        )
        if role_row is not None:
            return sanitize_permissions(role_row.permissions)

    return default_permissions_for(role)


def user_can_action(user, action_id: str, *, restaurant=None) -> bool:
    role = (getattr(user, "role", "") or "").upper()
    if role in PRIVILEGED_ROLES:
        return True
    if restaurant is not None:
        from miya.services.tenant import effective_role_at_tenant

        tenant_role = effective_role_at_tenant(user, restaurant)
        if tenant_role in PRIVILEGED_ROLES:
            return True
        if tenant_role == "MANAGER" and action_id in ACTION_IDS:
            return True
    if action_id not in ACTION_IDS:
        return False
    perms = effective_permissions(user, restaurant=restaurant)
    return action_id in (perms.get("actions") or [])


def miya_has_full_tenant_access(user, restaurant=None) -> bool:
    """Managers and privileged roles get every Miya tool within their tenant."""
    role = (getattr(user, "role", "") or "").upper()
    if role in PRIVILEGED_ROLES:
        return True
    if restaurant is not None:
        from miya.services.tenant import effective_role_at_tenant

        tenant_role = effective_role_at_tenant(user, restaurant)
        if tenant_role in PRIVILEGED_ROLES | {"MANAGER"}:
            return True
    return user_can_action(user, "miya_full_tools", restaurant=restaurant)


def user_can_use_miya(user) -> bool:
    """
    WhatsApp/dashboard Miya companion access.

    Any active user linked to a restaurant may chat with Miya (staff companion).
    Manager-only tools remain gated separately via TOOL_REQUIRED_ACTIONS.
    """
    if not user or not getattr(user, "is_active", False):
        return False
    role = (getattr(user, "role", "") or "").upper()
    if role in PRIVILEGED_ROLES:
        return True
    perms = effective_permissions(user)
    apps = set(perms.get("apps") or [])
    actions = set(perms.get("actions") or [])
    if "miya_full_tools" in actions:
        return True
    # Staff operational apps imply WhatsApp Miya access
    if apps & {"scheduling", "staff", "supervisor", "take_orders", "staff_requests", "inventory"}:
        return True
    if role in {"MANAGER", "SUPERVISOR", "OWNER", "ADMIN"}:
        return True
    # Activated staff with empty RolePermissionSet apps (CLEANER, CUSTOM, etc.)
    # must still reach Miya on WhatsApp for clock-in, tasks, and escalations.
    if getattr(user, "restaurant_id", None) and role:
        return True
    return False


# Tool name → required RBAC action (None = allowed for any Miya user).
TOOL_REQUIRED_ACTIONS: dict[str, str | None] = {
    # Staff companion (any Miya user)
    "my_shifts": None,
    "platform_knowledge": None,
    "staff_clock_in": None,
    "staff_clock_out": None,
    "staff_request": None,
    "request_time_off": None,
    "report_incident": None,
    "list_incidents": None,
    "find_incidents": None,
    "create_incident": None,
    "get_incident": None,
    "get_incident_photo": None,
    "route_incident": "manage_widgets",
    "confirm_meeting": None,
    "search_operational_records": None,
    "close_incident": "manage_widgets",
    "resolve_incident": "manage_widgets",
    "get_business_context": None,
    "list_dashboard_widgets": None,
    # Manager / elevated
    "staff_lookup": "miya_full_tools",
    "find_staff": "miya_full_tools",
    "list_shifts": "edit_schedule",
    "create_shift": "edit_schedule",
    "mark_no_show": "edit_schedule",
    "assign_coverage": "edit_schedule",
    "create_dashboard_task": "manage_widgets",
    "list_dashboard_tasks": "manage_widgets",
    "get_dashboard_task": "manage_widgets",
    "find_tasks": "manage_widgets",
    "update_dashboard_task_status": "manage_widgets",
    "reassign_dashboard_task": "manage_widgets",
    "update_dashboard_task": "manage_widgets",
    "find_category": "miya_full_tools",
    "find_category_owners": "miya_full_tools",
    "find_responsible_people": "miya_full_tools",
    "assign_responsibility": "manage_settings",
    "create_responsibility_category": "manage_settings",
    "create_category": "manage_settings",
    "route_responsibility_event": "manage_widgets",
    "find_establishment": "miya_full_tools",
    "find_establishments": "miya_full_tools",
    "set_establishment_context": "miya_full_tools",
    "switch_establishment": "miya_full_tools",
    "find_documents": None,
    "list_tenant_documents": None,
    "get_tenant_document": None,
    "get_document": None,
    "show_document": None,
    "query_document_intelligence": None,
    "find_invoices": "run_reports",
    "retrieve_operational_history": "miya_full_tools",
    "recall_operational_memory": "miya_full_tools",
    "get_event_history": "miya_full_tools",
    "assign_ops_task": "manage_widgets",
    "update_ops_task_status": "manage_widgets",
    "create_ops_task": "manage_widgets",
    "list_calendar_events": "manage_widgets",
    "list_meetings": "manage_widgets",
    "update_calendar_event": "manage_widgets",
    "delete_calendar_event": "manage_widgets",
    "create_personal_reminder": None,
    "list_reminders": None,
    "cancel_reminder": None,
    "sync_compliance_reminder": "manage_settings",
    "create_calendar_event": "manage_widgets",
    "list_invoices": "run_reports",
    "record_invoice": "run_reports",
    "find_invoices": "run_reports",
    "get_invoice": "run_reports",
    "check_invoice_approval": "run_reports",
    "request_invoice_approval": "run_reports",
    "payment_approval": "run_reports",
    "ops_search": "miya_full_tools",
    "chase_operational_record": "manage_widgets",
    "category_routing": "manage_settings",
    "create_custom_widget": "manage_widgets",
    "create_automation": "manage_settings",
    "list_automations": "manage_settings",
    "dashboard_widgets_add": "manage_widgets",
    "list_staff_requests": "miya_full_tools",
    "approve_staff_request": "miya_full_tools",
    "reject_staff_request": "miya_full_tools",
    "list_inventory": "edit_inventory",
    "report_waste": "edit_inventory",
    "sales_summary": "run_reports",
    "proactive_insights": "run_reports",
    "recognize_staff": "miya_full_tools",
    "send_announcement": "miya_full_tools",
    "list_compliance_documents": "manage_compliance_docs",
    "update_compliance_document": "manage_compliance_docs",
    "seed_compliance_documents": "manage_compliance_docs",
    "parse_photo": "run_reports",
    "parse_document": "run_reports",
    "mark_invoice_paid": "run_reports",
    "get_invoice_timeline": "run_reports",
    "attach_invoice_proof": "run_reports",
    "return_invoice": "run_reports",
    "assign_invoice": "run_reports",
    "cross_location_report": "run_reports",
    "location_detail": "run_reports",
    "list_operations_live": "manage_widgets",
    "notify_manager_urgent": "manage_widgets",
}


def allowed_tools_for_user(user, restaurant=None) -> set[str]:
    if not user_can_use_miya(user):
        return set()
    if miya_has_full_tenant_access(user, restaurant):
        return set(TOOL_REQUIRED_ACTIONS.keys())
    allowed = set()
    for tool_name, action_id in TOOL_REQUIRED_ACTIONS.items():
        if action_id is None or user_can_action(user, action_id, restaurant=restaurant):
            allowed.add(tool_name)
    return allowed


def rbac_catalog_for_docs() -> dict[str, Any]:
    return {
        "apps": APPS,
        "widgets": WIDGET_IDS,
        "actions": ACTIONS,
        "tool_actions": TOOL_REQUIRED_ACTIONS,
    }
