"""OpenAI tool schemas and execution against existing Mizan agent API routes."""

from __future__ import annotations

import json
import logging
from typing import Any

import requests
from django.conf import settings

from accounts.rbac_enforce import allowed_tools_for_user

logger = logging.getLogger(__name__)

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "staff_lookup",
            "description": (
                "Find staff at this workspace by name, role, or list all staff. "
                "Always pass restaurant_id from context. Use before assigning tasks."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "my_shifts",
            "description": "Get shifts for the current authenticated user (staff schedule).",
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "staff_id": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_shifts",
            "description": "List workspace shifts for a date or range (manager).",
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "date": {"type": "string"},
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_shift",
            "description": "Schedule a staff member for a shift.",
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "staff_id": {"type": "string"},
                    "start_time": {"type": "string"},
                    "end_time": {"type": "string"},
                    "role": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["restaurant_id", "staff_id", "start_time", "end_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "staff_clock_in",
            "description": (
                "Clock the staff member in by phone. On WhatsApp without GPS, Django "
                "sends Share Location. Relay the tool message verbatim. Never ask for "
                "opening float before location."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {"type": "string"},
                    "restaurant_id": {"type": "string"},
                    "latitude": {"type": "number"},
                    "longitude": {"type": "number"},
                },
                "required": ["phone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "staff_clock_out",
            "description": "Clock the staff member out by phone.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {"type": "string"},
                    "restaurant_id": {"type": "string"},
                },
                "required": ["phone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "staff_request",
            "description": (
                "Log a staff→manager request into the correct inbox lane. "
                "Categories: MAINTENANCE, PAYROLL, HR, DOCUMENT, SCHEDULING, "
                "INVENTORY, PURCHASE_ORDER, OPERATIONS, FINANCE, OTHER. "
                "Use for 'tell my manager…', repairs, wages, leave escalations."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "phone": {"type": "string"},
                    "user_id": {"type": "string"},
                    "message": {"type": "string"},
                    "category": {"type": "string"},
                    "title": {"type": "string"},
                },
                "required": ["restaurant_id", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_staff_requests",
            "description": "List pending staff requests for managers to approve.",
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "status": {"type": "string"},
                    "category": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "approve_staff_request",
            "description": "Approve a staff request by id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "request_id": {"type": "string"},
                },
                "required": ["restaurant_id", "request_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reject_staff_request",
            "description": "Reject a staff request by id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "request_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["restaurant_id", "request_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report_incident",
            "description": (
                "Report a SAFETY incident (slip, fire, injury, harassment, broken glass "
                "with harm risk). NOT for routine equipment repairs — use staff_request "
                "MAINTENANCE for those."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "description": {"type": "string"},
                    "phone": {"type": "string"},
                    "incident_type": {"type": "string"},
                    "severity": {"type": "string"},
                },
                "required": ["restaurant_id", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_time_off",
            "description": "Submit a time-off / leave request when dates are known.",
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "staff_id": {"type": "string"},
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["restaurant_id", "start_date", "end_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_dashboard_task",
            "description": (
                "Create a trackable dashboard task and WhatsApp the assignee by default. "
                "Use for deliverables with owners — not for pure 'tell X' pings."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "assignee_id": {"type": "string"},
                    "priority": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH", "URGENT"]},
                    "due_date": {"type": "string"},
                    "category": {"type": "string"},
                    "assign_to_self": {"type": "boolean"},
                    "notify_whatsapp": {"type": "boolean"},
                },
                "required": ["restaurant_id", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dashboard_widgets_add",
            "description": (
                "Add built-in dashboard widgets for the manager "
                "(operations, finance, human_resources, maintenance, purchase_orders, "
                "staff_inbox, incidents, etc.). Pass user_id of the logged-in manager."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "user_id": {"type": "string"},
                    "widgets": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["restaurant_id", "widgets"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_inventory",
            "description": "List inventory items for the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "query": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report_waste",
            "description": "Report inventory waste / spoilage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "item_name": {"type": "string"},
                    "quantity": {"type": "number"},
                    "unit": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["restaurant_id", "item_name", "quantity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sales_summary",
            "description": "Get POS / sales summary for the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "date": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recognize_staff",
            "description": "Send kudos / recognition to a staff member.",
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "staff_id": {"type": "string"},
                    "message": {"type": "string"},
                },
                "required": ["restaurant_id", "staff_id", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_no_show",
            "description": "Mark a staff member as no-show for a shift.",
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "shift_id": {"type": "string"},
                    "staff_id": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assign_coverage",
            "description": "Assign coverage for a gap / no-show.",
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "shift_id": {"type": "string"},
                    "staff_id": {"type": "string"},
                },
                "required": ["restaurant_id", "staff_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "platform_knowledge",
            "description": "Search Mizan product help (features, workflows). Not tenant SOP.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "audience": {"type": "string", "enum": ["manager", "staff"]},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "proactive_insights",
            "description": "Get operational insights (staffing, tasks, alerts).",
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_announcement",
            "description": (
                "Manager→staff broadcast / ping via app + WhatsApp. "
                "NOT for staff escalating their own issue to a manager."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "message": {"type": "string"},
                    "title": {"type": "string"},
                    "audience": {
                        "type": "object",
                        "properties": {
                            "staff_ids": {"type": "array", "items": {"type": "string"}},
                            "roles": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "required": ["restaurant_id", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_business_context",
            "description": "Load workspace details and vertical playbook for sector-aware replies.",
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
]


def tools_for_user(user) -> list[dict[str, Any]]:
    allowed = allowed_tools_for_user(user)
    if not allowed:
        return []
    return [
        schema
        for schema in TOOL_SCHEMAS
        if (schema.get("function") or {}).get("name") in allowed
    ]


_ROUTE_MAP: dict[str, tuple[str, str]] = {
    "staff_lookup": ("POST", "/api/scheduling/agent/staff/"),
    "my_shifts": ("POST", "/api/scheduling/agent/my-shifts/"),
    "list_shifts": ("POST", "/api/scheduling/agent/list-shifts/"),
    "create_shift": ("POST", "/api/scheduling/agent/create-shift/"),
    "staff_clock_in": ("POST", "/api/timeclock/agent/clock-in-by-phone/"),
    "staff_clock_out": ("POST", "/api/timeclock/agent/clock-out-by-phone/"),
    "staff_request": ("POST", "/api/staff/agent/requests/ingest/"),
    "list_staff_requests": ("POST", "/api/staff/agent/requests/"),
    "approve_staff_request": ("POST", "/api/staff/agent/requests/approve/"),
    "reject_staff_request": ("POST", "/api/staff/agent/requests/reject/"),
    "report_incident": ("POST", "/api/reporting/agent/create-incident/"),
    "request_time_off": ("POST", "/api/scheduling/agent/time-off/request/"),
    "create_dashboard_task": ("POST", "/api/dashboard/agent/tasks/create/"),
    "dashboard_widgets_add": ("POST", "/api/dashboard/agent/widgets/add/"),
    "list_inventory": ("POST", "/api/inventory/agent/items/"),
    "report_waste": ("POST", "/api/inventory/agent/waste/"),
    "sales_summary": ("POST", "/api/pos/agent/sales-summary/"),
    "recognize_staff": ("POST", "/api/agent/recognize-staff/"),
    "mark_no_show": ("POST", "/api/scheduling/agent/mark-no-show/"),
    "assign_coverage": ("POST", "/api/scheduling/agent/assign-coverage/"),
    "platform_knowledge": ("POST", "/api/agent/platform-knowledge/"),
    "proactive_insights": ("POST", "/api/scheduling/agent/proactive-insights/"),
    "send_announcement": ("POST", "/api/notifications/agent/announcement/"),
    "get_business_context": ("POST", "/api/scheduling/agent/restaurant-details/"),
}


def _api_base() -> str:
    return (getattr(settings, "MIYA_AGENT_API_BASE", None) or "http://127.0.0.1:8000").rstrip("/")


def _auth_headers(access_token: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    agent_key = getattr(settings, "LUA_WEBHOOK_API_KEY", "") or ""
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    elif agent_key:
        headers["Authorization"] = f"Bearer {agent_key}"
    return headers


def execute_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    access_token: str | None,
    session_context: dict[str, Any],
    user=None,
) -> dict[str, Any]:
    if user is not None and name not in allowed_tools_for_user(user):
        return {
            "success": False,
            "error": "You don't have permission for this action on Mizan.",
            "required_rbac": True,
        }

    route = _ROUTE_MAP.get(name)
    if not route:
        return {"success": False, "error": f"Unknown tool: {name}"}

    method, path = route
    payload = dict(arguments or {})

    rid = session_context.get("restaurant_id")
    if rid and "restaurant_id" not in payload:
        payload["restaurant_id"] = rid

    phone = session_context.get("user_phone")
    uid = session_context.get("user_id")

    if name == "my_shifts" and not payload.get("staff_id"):
        payload["staff_id"] = uid

    if name in ("staff_clock_in", "staff_clock_out") and not payload.get("phone"):
        payload["phone"] = phone

    if name == "staff_request":
        if not payload.get("phone") and phone:
            payload["phone"] = phone
        if not payload.get("user_id") and uid:
            payload["user_id"] = uid
        if not payload.get("concern") and payload.get("message"):
            payload["concern"] = payload["message"]

    if name == "report_incident":
        if not payload.get("phone") and phone:
            payload["phone"] = phone
        if not payload.get("description") and payload.get("message"):
            payload["description"] = payload["message"]

    if name == "request_time_off" and not payload.get("staff_id"):
        payload["staff_id"] = uid

    if name == "create_dashboard_task" and payload.get("assign_to_self") and not payload.get("assignee_id"):
        payload["assignee_id"] = uid

    if name == "dashboard_widgets_add" and not payload.get("user_id"):
        payload["user_id"] = uid

    if name == "platform_knowledge":
        payload.setdefault("q", payload.pop("query", ""))
        role = (session_context.get("role") or "MANAGER").upper()
        default_aud = "staff" if session_context.get("channel") == "whatsapp" and role not in {
            "MANAGER", "ADMIN", "SUPER_ADMIN", "OWNER",
        } else "manager"
        payload.setdefault("audience", default_aud)
        if payload.get("audience") not in ("manager", "staff"):
            payload["audience"] = "manager"

    if name == "send_announcement":
        payload["sender_id"] = uid

    url = f"{_api_base()}{path}"
    try:
        resp = requests.request(
            method,
            url,
            headers=_auth_headers(access_token),
            json=payload,
            timeout=45,
        )
    except requests.RequestException as exc:
        logger.warning("Miya tool %s request failed: %s", name, exc)
        return {"success": False, "error": str(exc)}

    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text[:500]}

    if resp.status_code >= 400:
        return {
            "success": False,
            "status_code": resp.status_code,
            "error": body.get("error") if isinstance(body, dict) else body,
            "details": body,
        }

    return {"success": True, "data": body}


def serialize_tool_result(result: dict[str, Any]) -> str:
    return json.dumps(result, default=str)[:8000]
