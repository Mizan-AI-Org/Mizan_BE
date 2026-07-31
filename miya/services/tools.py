"""OpenAI tool schemas and execution against existing Mizan agent API routes."""

from __future__ import annotations

import json
import logging
from typing import Any

import requests
from django.conf import settings

from accounts.rbac_enforce import allowed_tools_for_user
from miya.services.tenant import bind_tool_payload_to_tenant, resolve_active_tenant

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
            "description": (
                "List workspace shifts for a date or range (manager). "
                "Use for 'who is on duty today', 'who is scheduled', staffing coverage. "
                "Pass date or start_date/end_date as YYYY-MM-DD (default: today)."
            ),
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
            "description": (
                "Schedule a staff member for a shift. Provide staff_name (e.g. Adama) "
                "or staff_id. Always include shift_date (YYYY-MM-DD, default today). "
                "For dinner service use start_time 18:00 and end_time 23:00; lunch 11:00–15:00. "
                "Call staff_lookup first if you only have a first name."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "staff_id": {"type": "string", "description": "Staff UUID (optional if staff_name given)"},
                    "staff_name": {"type": "string", "description": "First or full name, e.g. Adama"},
                    "shift_date": {"type": "string", "description": "YYYY-MM-DD (default: today)"},
                    "date": {"type": "string", "description": "Alias for shift_date"},
                    "start_time": {"type": "string", "description": "HH:MM local time"},
                    "end_time": {"type": "string", "description": "HH:MM local time"},
                    "role": {"type": "string"},
                    "notes": {"type": "string", "description": "e.g. dinner service"},
                    "service": {"type": "string", "description": "dinner, lunch, or breakfast"},
                },
                "required": ["restaurant_id"],
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
            "name": "list_dashboard_tasks",
            "description": (
                "List dashboard tasks (pending, in progress, overdue). "
                "Use before status questions when task_id is unknown."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "status": {"type": "string"},
                    "assignee_id": {"type": "string"},
                    "overdue": {"type": "boolean"},
                    "limit": {"type": "integer"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_dashboard_task",
            "description": (
                "Get one task by UUID or short ref (e.g. 7FFC0D68 from 'Task #7FFC0D68'). "
                "Use for 'what is the status of this task?'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "task_ref": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_dashboard_task_status",
            "description": (
                "Change task status: PENDING, ACCEPTED, IN_PROGRESS, COMPLETED, "
                "UNABLE_TO_COMPLETE, CANCELLED. Use task_id or short ref (#7FFC0D68)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "task_ref": {"type": "string"},
                    "status": {"type": "string"},
                },
                "required": ["restaurant_id", "status"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reassign_dashboard_task",
            "description": (
                "Reassign a dashboard task to another staff member. "
                "Use staff_lookup first if you only have a name."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "task_ref": {"type": "string"},
                    "assignee_id": {"type": "string"},
                    "staff_name": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_dashboard_task",
            "description": (
                "Update task title, priority, due_date, description, or require_photo_proof."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "task_ref": {"type": "string"},
                    "title": {"type": "string"},
                    "priority": {"type": "string"},
                    "due_date": {"type": "string"},
                    "description": {"type": "string"},
                    "require_photo_proof": {"type": "boolean"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_calendar_event",
            "description": (
                "Create Google Calendar meeting(s) or reminders for the tenant. "
                "Supports batch via events[] for multiple meetings. "
                "Requires Google Calendar connected in Settings."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "title": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "description": {"type": "string"},
                    "location": {"type": "string"},
                    "attendees": {"type": "array", "items": {"type": "string"}},
                    "is_reminder": {"type": "boolean"},
                    "events": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_personal_reminder",
            "description": (
                "Create a personal reminder (insurance renewal, deadlines). "
                "Appears in dashboard + fires WhatsApp at due time."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "title": {"type": "string"},
                    "due_at": {"type": "string"},
                    "body": {"type": "string"},
                    "recurrence": {"type": "string"},
                    "attachment_url": {"type": "string"},
                },
                "required": ["restaurant_id", "title", "due_at"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_invoices",
            "description": (
                "List invoices for the tenant — open, overdue, by vendor. "
                "Use for 'what happened with Ahmed's invoice?' history questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "status": {"type": "string"},
                    "vendor": {"type": "string"},
                    "overdue": {"type": "boolean"},
                    "limit": {"type": "integer"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ops_search",
            "description": (
                "Search tasks, staff, requests across the workspace by keyword."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "q": {"type": "string"},
                },
                "required": ["restaurant_id", "q"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_automation",
            "description": (
                "Create a WhatsApp ops automation for this workspace. "
                "Prefer template_id when it fits: sales_process (sales/inquiry/quote), "
                "lead_qualifier, keyword_vip, welcome_message, out_of_office, follow_up_reminder. "
                "Each step MUST use type + config (never action/message at top level only). "
                "Valid step types: send_message, add_tag, remove_tag, create_task, "
                "create_staff_request, wait, condition, send_webhook, close_conversation. "
                "Example steps: "
                '[{"type":"add_tag","config":{"tag":"SALES_INQUIRY"}}, '
                '{"type":"send_message","config":{"text":"Thanks — we will follow up shortly."}}]. '
                "For sales flows use template_id sales_process or keywords like quote, price, inquiry."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "template_id": {
                        "type": "string",
                        "enum": [
                            "sales_process",
                            "lead_qualifier",
                            "keyword_vip",
                            "welcome_message",
                            "out_of_office",
                            "follow_up_reminder",
                        ],
                    },
                    "trigger_type": {
                        "type": "string",
                        "enum": [
                            "new_message_received",
                            "first_message_from_contact",
                            "keyword_match",
                            "tag_added",
                            "time_based",
                        ],
                    },
                    "trigger_config": {"type": "object"},
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "For keyword_match trigger",
                    },
                    "message": {
                        "type": "string",
                        "description": "Optional auto-reply text (also applied to send_message steps)",
                    },
                    "tag": {
                        "type": "string",
                        "description": "Optional contact tag (add_tag step)",
                    },
                    "steps": {
                        "type": "array",
                        "description": "Ordered actions — each item needs type and config",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": [
                                        "send_message",
                                        "add_tag",
                                        "remove_tag",
                                        "create_task",
                                        "create_staff_request",
                                        "wait",
                                        "condition",
                                        "send_webhook",
                                        "close_conversation",
                                    ],
                                },
                                "config": {"type": "object"},
                            },
                            "required": ["type", "config"],
                        },
                    },
                    "is_active": {"type": "boolean"},
                    "stop_miya_on_match": {"type": "boolean"},
                },
                "required": ["restaurant_id", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_automations",
            "description": "List automations configured for this workspace.",
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
            "name": "chase_operational_record",
            "description": (
                "Send an immediate WhatsApp follow-up ping for a pending task or staff request. "
                "Use when the manager asks to chase, remind, or 'update now' on an assignment."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "record_id": {"type": "string"},
                    "record_type": {"type": "string", "enum": ["dashboard_task", "staff_request"]},
                    "q": {"type": "string", "description": "Search query if record_id unknown"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_invoice",
            "description": (
                "Record a supplier invoice for PayGuard approval. Requires vendor, amount, due_date. "
                "If amount exceeds approval limit, PayGuard routes to the configured approver automatically."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "vendor_name": {"type": "string"},
                    "amount": {"type": "number"},
                    "currency": {"type": "string"},
                    "due_date": {"type": "string"},
                    "invoice_number": {"type": "string"},
                    "notes": {"type": "string"},
                    "photo_url": {"type": "string"},
                },
                "required": ["restaurant_id", "vendor_name", "amount", "due_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "payment_approval",
            "description": (
                "PayGuard payment approval: list pending, approve, reject, or read policy. "
                "Use when an invoice exceeds the approval limit or manager asks about payment approval."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "action": {
                        "type": "string",
                        "enum": ["list", "approve", "reject", "policy"],
                    },
                    "approval_id": {"type": "string"},
                    "invoice_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "category_routing",
            "description": (
                "Read or update category routing (multiple responsible users per HR/Finance/Maintenance lane). "
                "action: get | set. For set, pass category_owners object mapping category slug to user id list."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "action": {"type": "string", "enum": ["get", "set"]},
                    "category_owners": {"type": "object"},
                },
                "required": ["restaurant_id", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_custom_widget",
            "description": (
                "Create a bespoke dashboard widget with optional routing_keywords so tasks auto-route "
                "when titles match (e.g. 'Daily sales', 'Kitchen status')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "user_id": {"type": "string"},
                    "title": {"type": "string"},
                    "subtitle": {"type": "string"},
                    "routing_keywords": {"type": "array", "items": {"type": "string"}},
                    "category_name": {"type": "string"},
                    "add_to_dashboard": {"type": "boolean"},
                },
                "required": ["restaurant_id", "title"],
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


def tools_for_user(user, restaurant=None) -> list[dict[str, Any]]:
    allowed = allowed_tools_for_user(user, restaurant=restaurant)
    if not allowed:
        return []
    return [
        schema
        for schema in TOOL_SCHEMAS
        if (schema.get("function") or {}).get("name") in allowed
    ]


_ROUTE_MAP: dict[str, tuple[str, str]] = {
    "staff_lookup": ("POST", "/api/scheduling/agent/staff/"),
    "my_shifts": ("GET", "/api/scheduling/agent/my-shifts/"),
    "list_shifts": ("GET", "/api/scheduling/agent/list-shifts/"),
    "create_shift": ("POST", "/api/scheduling/agent/create-shift/"),
    "staff_clock_in": ("POST", "/api/timeclock/agent/clock-in-by-phone/"),
    "staff_clock_out": ("POST", "/api/timeclock/agent/clock-out-by-phone/"),
    "staff_request": ("POST", "/api/staff/agent/requests/ingest/"),
    "list_staff_requests": ("GET", "/api/staff/agent/requests/"),
    "approve_staff_request": ("POST", "/api/staff/agent/requests/approve/"),
    "reject_staff_request": ("POST", "/api/staff/agent/requests/reject/"),
    "report_incident": ("POST", "/api/reporting/agent/create-incident/"),
    "request_time_off": ("POST", "/api/scheduling/agent/time-off/request/"),
    "create_dashboard_task": ("POST", "/api/dashboard/agent/tasks/create/"),
    "list_dashboard_tasks": ("POST", "/api/dashboard/agent/tasks/list/"),
    "get_dashboard_task": ("POST", "/api/dashboard/agent/tasks/list/"),
    "update_dashboard_task_status": ("POST", "/api/dashboard/agent/tasks/status/"),
    "reassign_dashboard_task": ("POST", "/api/dashboard/agent/tasks/reassign/"),
    "update_dashboard_task": ("POST", "/api/dashboard/agent/tasks/update/"),
    "create_calendar_event": ("POST", "/api/dashboard/agent/calendar-events/create/"),
    "create_personal_reminder": ("POST", "/api/scheduling/agent/personal-reminders/"),
    "list_invoices": ("POST", "/api/finance/agent/invoices/list/"),
    "ops_search": ("GET", "/api/dashboard/agent/search/"),
    "chase_operational_record": ("POST", "/api/staff/agent/records/chase/"),
    "record_invoice": ("POST", "/api/finance/agent/invoices/record/"),
    "payment_approval": ("POST", "/api/finance/agent/payment-approval/"),
    "category_routing": ("POST", "/api/dashboard/agent/department-owners/"),
    "create_custom_widget": ("POST", "/api/dashboard/agent/widgets/create/"),
    "create_automation": ("POST", "/api/automations/agent/create/"),
    "list_automations": ("POST", "/api/automations/agent/list/"),
    "dashboard_widgets_add": ("POST", "/api/dashboard/agent/widgets/add/"),
    "list_inventory": ("GET", "/api/inventory/agent/items/"),
    "report_waste": ("POST", "/api/inventory/agent/waste/"),
    "sales_summary": ("GET", "/api/pos/agent/sales-summary/"),
    "recognize_staff": ("POST", "/api/agent/recognize-staff/"),
    "mark_no_show": ("POST", "/api/scheduling/agent/mark-no-show/"),
    "assign_coverage": ("POST", "/api/scheduling/agent/assign-coverage/"),
    "platform_knowledge": ("POST", "/api/agent/platform-knowledge/"),
    "proactive_insights": ("GET", "/api/scheduling/agent/proactive-insights/"),
    "send_announcement": ("POST", "/api/notifications/agent/announcement/"),
    "get_business_context": ("GET", "/api/scheduling/agent/restaurant-details/"),
}

# Tools that must use GET (query params) — kept in sync with scheduling agent views.
_GET_METHOD_TOOLS = frozenset(
    {
        "list_shifts",
        "my_shifts",
        "proactive_insights",
        "get_business_context",
        "ops_search",
        "list_staff_requests",
        "list_inventory",
        "sales_summary",
    }
)


def _api_base() -> str:
    base = (getattr(settings, "MIYA_AGENT_API_BASE", None) or "").strip()
    if base:
        return base.rstrip("/")
    return "http://127.0.0.1:8000"


def _auth_headers(access_token: str | None, session_context: dict[str, Any] | None = None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    agent_key = getattr(settings, "LUA_WEBHOOK_API_KEY", "") or ""
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    elif agent_key:
        headers["Authorization"] = f"Bearer {agent_key}"

    ctx = session_context or {}
    rid = ctx.get("restaurant_id")
    if rid:
        headers["X-Restaurant-Id"] = str(rid)
    return headers


def _enrich_agent_payload(
    name: str,
    payload: dict[str, Any],
    session_context: dict[str, Any],
) -> dict[str, Any]:
    uid = session_context.get("user_id")
    phone = session_context.get("user_phone")

    if uid:
        payload.setdefault("user_id", uid)
        payload.setdefault("userId", uid)
    if phone:
        payload.setdefault("phone", phone)

    if name == "get_dashboard_task":
        task_ref = payload.pop("task_ref", None) or payload.get("task_id")
        if task_ref and not payload.get("task_id"):
            payload["task_id"] = str(task_ref).strip().lstrip("#")
        payload.setdefault("limit", 1)

    if name in (
        "update_dashboard_task_status",
        "reassign_dashboard_task",
        "update_dashboard_task",
    ):
        task_ref = payload.pop("task_ref", None) or payload.get("task_id")
        if task_ref and not payload.get("task_id"):
            payload["task_id"] = str(task_ref).strip().lstrip("#")

    if name == "create_personal_reminder":
        payload.setdefault("action", "create")
        if payload.get("title") and not payload.get("text"):
            payload["text"] = payload["title"]

    if name == "ops_search" and payload.get("q"):
        payload["q"] = str(payload["q"]).strip()

    if name == "list_shifts":
        from datetime import date as date_cls

        today = date_cls.today().isoformat()
        if payload.get("date") and not payload.get("date_from"):
            d = str(payload["date"]).strip()[:10]
            payload["date_from"] = d
            payload["date_to"] = d
        if payload.get("start_date") and not payload.get("date_from"):
            payload["date_from"] = str(payload["start_date"]).strip()[:10]
        if payload.get("end_date") and not payload.get("date_to"):
            payload["date_to"] = str(payload["end_date"]).strip()[:10]
        payload["date_from"] = str(payload.get("date_from") or today)[:10]
        payload["date_to"] = str(payload.get("date_to") or today)[:10]

    if name == "create_shift":
        from core.agent_params import enrich_create_shift_payload

        payload = enrich_create_shift_payload(payload)

    return payload


def execute_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    access_token: str | None,
    session_context: dict[str, Any],
    user=None,
) -> dict[str, Any]:
    tenant_rest = None
    rid = (session_context or {}).get("restaurant_id")
    if rid:
        from accounts.models import Restaurant

        try:
            tenant_rest = Restaurant.objects.filter(id=rid).first()
        except Exception:
            tenant_rest = None
    if tenant_rest is None and user is not None:
        tenant_rest = resolve_active_tenant(user, session_hint=session_context)

    if user is not None and name not in allowed_tools_for_user(user, restaurant=tenant_rest):
        return {
            "success": False,
            "error": "You don't have permission for this action on Mizan.",
            "required_rbac": True,
        }

    route = _ROUTE_MAP.get(name)
    if not route:
        return {"success": False, "error": f"Unknown tool: {name}"}

    method, path = route
    if name in _GET_METHOD_TOOLS:
        method = "GET"
    if name == "category_routing" and str((arguments or {}).get("action") or "get").lower() == "get":
        method = "GET"
    payload = dict(arguments or {})

    payload, tenant_err = bind_tool_payload_to_tenant(user, payload, session_context)
    if tenant_err:
        return tenant_err

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

    if name == "create_custom_widget" and not payload.get("user_id"):
        payload["user_id"] = uid

    if name == "payment_approval" and not payload.get("action"):
        payload["action"] = "list"

    if name == "category_routing":
        act = str(payload.get("action") or "get").lower()
        payload["action"] = act
        if act == "set" and payload.get("category_owners"):
            payload.update(payload["category_owners"])

    if name == "record_invoice" and payload.get("vendor") and not payload.get("vendor_name"):
        payload["vendor_name"] = payload["vendor"]

    payload = _enrich_agent_payload(name, payload, session_context)

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
    headers = _auth_headers(access_token, session_context)
    try:
        from .tool_dispatch import dispatch_agent_request, should_dispatch_in_process

        if should_dispatch_in_process(_api_base()):
            # In-process agent views authenticate via LUA key; user/tenant context is in payload.
            agent_key = getattr(settings, "LUA_WEBHOOK_API_KEY", "") or ""
            if agent_key:
                headers = {**headers, "Authorization": f"Bearer {agent_key}"}
            status_code, body = dispatch_agent_request(
                method,
                path,
                json_payload=payload,
                headers=headers,
            )
        else:
            resp = requests.request(
                method,
                url,
                headers=headers,
                json=payload,
                timeout=45,
            )
            status_code = resp.status_code
            try:
                body = resp.json()
            except ValueError:
                body = {"raw": resp.text[:500]}
    except requests.RequestException as exc:
        logger.warning("Miya tool %s request failed: %s", name, exc)
        return {"success": False, "error": str(exc)}

    if status_code >= 400:
        from miya.services.user_errors import pick_user_message, sanitize_user_error

        body_dict = body if isinstance(body, dict) else {}
        user_msg = pick_user_message(body_dict)
        return {
            "success": False,
            "status_code": status_code,
            "error": sanitize_user_error(body_dict.get("error") or user_msg),
            "message_for_user": user_msg,
            "details": body,
        }

    if isinstance(body, dict) and body.get("message_for_user"):
        from miya.services.user_errors import sanitize_user_error

        body = {**body, "message_for_user": sanitize_user_error(body["message_for_user"])}

    return {"success": True, "data": body}


def serialize_tool_result(result: dict[str, Any]) -> str:
    return json.dumps(result, default=str)[:8000]
