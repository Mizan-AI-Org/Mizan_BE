"""OpenAI tool schemas and execution against existing Mizan agent API routes."""

from __future__ import annotations

import json
import logging
from typing import Any

import requests
from django.conf import settings

from accounts.rbac_enforce import allowed_tools_for_user
from core.agent_auth import is_agent_bearer, primary_agent_bearer_token
from miya.services.tenant import bind_tool_payload_to_tenant, resolve_active_tenant

logger = logging.getLogger(__name__)

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "staff_lookup",
            "description": (
                "Find staff at this workspace by name, role, department/tag, or list all staff. "
                "Aliases: find_staff. Always pass restaurant_id from context. "
                "Use for 'who works in the kitchen', before assigning tasks."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                    "tag": {"type": "string", "description": "e.g. KITCHEN, BAR, SERVICE"},
                    "q": {"type": "string", "description": "Free-text name or department"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_staff",
            "description": (
                "Canonical staff search (same as staff_lookup). Tenant-scoped. "
                "Use for 'who works in the kitchen/bar/service' or name lookup."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                    "tag": {"type": "string"},
                    "q": {"type": "string"},
                    "limit": {"type": "integer"},
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
                "Report a REAL SAFETY incident that just happened (slip, fire, injury, "
                "harassment, broken glass with harm). NEVER call for status checks like "
                "'rien à signaler?', 'any incidents?', 'nothing to report', or when the "
                "manager sends a task-board screenshot. For those: list_operations_live / "
                "list_dashboard_tasks. Equipment repairs → staff_request MAINTENANCE."
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
            "name": "list_incidents",
            "description": (
                "List or look up safety / maintenance incidents from Checklist & Incidences. "
                "Use for status questions ('has the fridge been repaired?', 'any open incidents?'). "
                "Use q with keywords from the user's question (e.g. 'computer screen', 'fridge'). "
                "When q is set, all statuses are searched (OPEN and RESOLVED). "
                "Call BEFORE close_incident / get_incident_photo when incident_id is unknown. "
                "Returns has_photo / photo_count — use get_incident_photo to show the image."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "description": "OPEN (default) | UNDER_REVIEW | RESOLVED | ALL",
                    },
                    "q": {
                        "type": "string",
                        "description": "Search title/description (e.g. 'fridge', 'maintenance').",
                    },
                    "limit": {"type": "integer"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_incident",
            "description": (
                "Get one incident's full detail including whether a photo is attached "
                "and secure photo references. Prefer list_incidents(q=…) first when id unknown."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "incident_id": {"type": "string"},
                    "q": {
                        "type": "string",
                        "description": "Keyword when incident_id unknown (e.g. 'refrigerator').",
                    },
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_incident_photo",
            "description": (
                "Show / retrieve the photo attached to an incident. "
                "Use when the user asks to see the photo (e.g. 'Show me the photo attached to "
                "the refrigerator incident'). On WhatsApp this sends the stored image; "
                "on dashboard it returns a secure document/image reference. "
                "Call list_incidents(q=…) first when incident_id is unknown."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "incident_id": {"type": "string"},
                    "q": {
                        "type": "string",
                        "description": "Keyword when incident_id unknown (e.g. 'refrigerator').",
                    },
                    "index": {
                        "type": "integer",
                        "description": "0-based photo index when multiple photos exist.",
                    },
                    "phone": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "route_incident",
            "description": "Re-route an open incident to the configured category owner.",
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "incident_id": {"type": "string"},
                    "incident_type": {"type": "string"},
                },
                "required": ["restaurant_id", "incident_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confirm_meeting",
            "description": (
                "Confirm attendance for an upcoming meeting/reminder when supported "
                "(personal reminder / calendar-linked). Use after approach pings."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "q": {"type": "string"},
                    "title": {"type": "string"},
                    "event_id": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_incident",
            "description": (
                "Close/resolve an incident from Checklist & Incidences. "
                "Always list_incidents with q first when incident_id is unknown. "
                "Do NOT use update_dashboard_task_status for incidents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "incident_id": {"type": "string"},
                    "resolution_notes": {"type": "string"},
                },
                "required": ["restaurant_id", "incident_id"],
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
                "Delegate a trackable task to a STAFF member (not the manager). "
                "Tasks auto-route to custom widgets when title/description/source_text "
                "matches routing_keywords. NEVER use for the manager's own reminders — "
                "use create_personal_reminder, create_calendar_event, or compliance tools. "
                "NEVER set assign_to_self for managers. Staff without manage_widgets: "
                "assign_to_self=true only."
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
                    "assign_to_category": {
                        "type": "string",
                        "description": (
                            "When the manager delegates to a lane without naming a person "
                            "(e.g. 'tell HR to pay all staff'), set PAYROLL or HR — "
                            "resolves the configured category owner as assignee."
                        ),
                    },
                    "assign_to_self": {"type": "boolean"},
                    "notify_whatsapp": {"type": "boolean"},
                    "custom_widget_id": {
                        "type": "string",
                        "description": "UUID of a custom widget tile (from list_dashboard_widgets).",
                    },
                    "widget_title": {
                        "type": "string",
                        "description": "Human widget name hint (e.g. Wedding) when id unknown.",
                    },
                    "source_text": {
                        "type": "string",
                        "description": "Original user message for keyword routing into widgets.",
                    },
                },
                "required": ["restaurant_id", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dashboard_widgets",
            "description": (
                "List dashboard widget layout and routing_catalog for the workspace. "
                "routing_catalog has every custom tile with routing_keywords — use before "
                "create_dashboard_task when filing items onto Wedding/Event/etc. widgets."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "user_id": {
                        "type": "string",
                        "description": "Manager user_id for layout; defaults to caller.",
                    },
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dashboard_tasks",
            "description": (
                "List dashboard Task rows only (subset of Operations Live). "
                "Does NOT include staff inbox requests, invoices, or scheduling tasks. "
                "For 'pending tasks', 'what is open today', or Operations Live → use "
                "list_operations_live instead and report every row in pending[]."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "description": "OPEN (default) | PENDING | ACCEPTED | IN_PROGRESS | COMPLETED | CANCELLED | ALL",
                    },
                    "q": {
                        "type": "string",
                        "description": "Search title/description (e.g. 'Maxime', 'Dj Zia').",
                    },
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
            "name": "list_operations_live",
            "description": (
                "Read the Operations Live board: new demands + in progress. "
                "REQUIRED for 'where are we at today', status updates, pending tasks, "
                "'what needs attention', or before updating lane items. "
                "Relay message_for_user / pending_summary verbatim — one concise briefing, "
                "critical items first. Set urgent_only=true for critical-only filter."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "q": {"type": "string"},
                    "search_by": {
                        "type": "string",
                        "enum": ["staff", "task", "category"],
                    },
                    "urgent_only": {"type": "boolean"},
                    "limit": {"type": "integer"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cross_location_report",
            "description": (
                "Compare all branches: staff count, clocked-in now, open requests by priority. "
                "Use for 'how is Marrakech vs Casablanca?', 'which branch is busiest?', "
                "or 'across all my locations'. period: today | week | month."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "period": {
                        "type": "string",
                        "description": "today (default) | week | month",
                    },
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "location_detail",
            "description": (
                "Live ops for ONE branch: team, coverage, labor today, shifts, staff roster. "
                "Use for 'how is Marrakech doing?', 'who is clocked in at the downtown branch?'. "
                "Pass location_name (partial match) or location_id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "location_id": {"type": "string"},
                    "location_name": {
                        "type": "string",
                        "description": "Branch name e.g. Marrakech, Casablanca",
                    },
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notify_manager_urgent",
            "description": (
                "Alert managers (in-app + WhatsApp) about pressing Operations Live items. "
                "Use when something is critical/urgent and the manager must be updated now, "
                "or when the user asks to 'ping the manager' / 'escalate this'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "message": {
                        "type": "string",
                        "description": "Optional custom alert text. If omitted, a summary of urgent lanes is sent.",
                    },
                    "task_id": {
                        "type": "string",
                        "description": "Optional task/request id to highlight.",
                    },
                    "channels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Default ['app','whatsapp'].",
                    },
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
                "Get current DB state of one task by UUID, short ref, title, or assignee query. "
                "Use for 'is Ahmed's task completed?', 'status of closing checklist'. "
                "Never invent status — only relay verified tool results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "task_ref": {"type": "string"},
                    "title": {"type": "string"},
                    "q": {"type": "string"},
                    "assignee_name": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_tasks",
            "description": (
                "Search tasks by title, status, or assignee name. Tenant-scoped. "
                "Prefer this for 'find Ahmed's open tasks' / 'closing checklist'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "q": {"type": "string"},
                    "title": {"type": "string"},
                    "status": {"type": "string"},
                    "assignee_name": {"type": "string"},
                    "task_id": {"type": "string"},
                    "limit": {"type": "integer"},
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
                "UNABLE_TO_COMPLETE, CANCELLED (remove from board). "
                "Resolve by task_id/short ref OR title/q ('Payer Dj Zia', 'photos pour Maxime'). "
                "For 'remove/cancel/enlever' use CANCELLED. For 'it's paid/done' use COMPLETED."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "task_ref": {"type": "string"},
                    "title": {
                        "type": "string",
                        "description": "Task title fragment when id unknown.",
                    },
                    "q": {"type": "string"},
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
                "Assign/reassign a dashboard task to another staff member. "
                "Resolve by task_id/short ref OR title/q. "
                "If the user says 'assign it to X' without a clear task, ask which task — do not guess. "
                "Use find_staff/staff_lookup first if you only have a name. "
                "Never say Done unless success=true and verified=true."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "task_ref": {"type": "string"},
                    "title": {"type": "string"},
                    "q": {"type": "string"},
                    "assignee_id": {"type": "string"},
                    "assignee_name": {"type": "string"},
                    "staff_name": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_incidents",
            "description": (
                "Find safety/ops incidents by day window, status, or text. "
                "Use for 'show today's incidents', 'incident from yesterday'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "q": {"type": "string"},
                    "status": {"type": "string"},
                    "since": {"type": "string", "description": "today | yesterday"},
                    "days": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_category_owners",
            "description": (
                "Who is responsible for a category (finance, HR, maintenance, …). "
                "Aliases: find_responsible_people, find_category."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "category": {"type": "string"},
                    "q": {"type": "string"},
                    "kind": {"type": "string", "description": "omit or 'incident'"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_category",
            "description": "Same as find_category_owners — resolve who owns a category.",
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "category": {"type": "string"},
                    "q": {"type": "string"},
                    "kind": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_responsible_people",
            "description": "Same as find_category_owners. Use for 'who should receive this incident?'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "category": {"type": "string"},
                    "q": {"type": "string"},
                    "kind": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assign_responsibility",
            "description": (
                "Assign one or more responsible people to a category "
                "(Settings → Who owns what). Supports multiple owners "
                "(e.g. INCIDENT → Manager + HR). Optional location_id for "
                "establishment-scoped ownership. Verify before saying Done."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "category": {"type": "string"},
                    "owner_name": {"type": "string"},
                    "owner_id": {"type": "string"},
                    "owner_names": {"type": "array", "items": {"type": "string"}},
                    "owner_ids": {"type": "array", "items": {"type": "string"}},
                    "staff_name": {"type": "string"},
                    "location_id": {"type": "string"},
                    "strategy": {
                        "type": "string",
                        "description": "notify_all | first_available | round_robin",
                    },
                },
                "required": ["restaurant_id", "category"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_responsibility_category",
            "description": (
                "Create a responsibility category (e.g. ORDERS, DELIVERIES) "
                "then use assign_responsibility to set owners."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "code": {"type": "string"},
                    "label": {"type": "string"},
                    "kind": {"type": "string", "description": "request | task | incident | mixed"},
                    "slugs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["restaurant_id", "code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "route_responsibility_event",
            "description": (
                "Route an event to category owners: resolve people, optionally "
                "create a task assignment, notify on WhatsApp, write audit."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "category": {"type": "string"},
                    "kind": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "create_task": {"type": "boolean"},
                    "location_id": {"type": "string"},
                    "entity_id": {"type": "string"},
                    "notify": {"type": "boolean"},
                },
                "required": ["restaurant_id", "category"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_establishments",
            "description": "List/find establishments (branches) for this tenant/org.",
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "q": {"type": "string"},
                    "name": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_establishment",
            "description": "Same as find_establishments — resolve a branch/location by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "q": {"type": "string"},
                    "name": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_establishment_context",
            "description": (
                "Switch sticky establishment/branch context for this user. "
                "Use when the user says 'What about Casablanca?', 'switch to Marrakech', "
                "or picks a branch after you asked which establishment. "
                "Subsequent task/incident/invoice/document queries use this location."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "location_id": {"type": "string"},
                    "q": {"type": "string"},
                    "name": {"type": "string"},
                    "location_name": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "switch_establishment",
            "description": "Alias for set_establishment_context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "location_id": {"type": "string"},
                    "q": {"type": "string"},
                    "name": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_documents",
            "description": (
                "Search compliance docs, Miya uploads, and invoices using STRUCTURED fields "
                "(vendor, amount, expiry_date) — not raw OCR alone. "
                "Use for insurance expiry, document lists, invoices uploaded yesterday. "
                "Pass since=yesterday for day filters."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "q": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "description": "all | compliance | tenant | invoice",
                    },
                    "category": {"type": "string"},
                    "since": {"type": "string", "description": "yesterday | today"},
                    "days": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_document",
            "description": (
                "Get one document's structured fields (vendor, amount, expiry) plus summary. "
                "Prefer over guessing from OCR. Use document_id or q."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "document_id": {"type": "string"},
                    "q": {"type": "string"},
                    "kind": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_document",
            "description": (
                "Show / send a stored document (e.g. 'Show me the insurance document'). "
                "On WhatsApp sends the file when possible; otherwise a secure dashboard reference."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "document_id": {"type": "string"},
                    "q": {"type": "string"},
                    "phone": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_document_intelligence",
            "description": (
                "Answer document questions from STRUCTURED extraction: insurance expiry, "
                "invoice amount, supplier/vendor, yesterday's invoice upload. "
                "Always prefer this (or get_document) over inventing values."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "question": {"type": "string"},
                    "q": {"type": "string"},
                    "document_id": {"type": "string"},
                    "since": {"type": "string"},
                    "days": {"type": "integer"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_invoices",
            "description": (
                "Find invoices by vendor/query with optional since=yesterday. "
                "Returns structured amount, vendor, due_date."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "q": {"type": "string"},
                    "vendor": {"type": "string"},
                    "since": {"type": "string"},
                    "days": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retrieve_operational_history",
            "description": (
                "Retrieve recent operational history across tasks, incidents, staff requests, "
                "invoices, and uploaded documents (database records)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "q": {"type": "string"},
                    "days": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_operational_memory",
            "description": (
                "Reconstruct operational memory from the database + durable events. "
                "Use for: 'What happened with the freezer incident?', timelines, "
                "assignments, status changes. Never invent history from chat."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {"type": "string", "description": "Keyword e.g. freezer, decoration"},
                    "entity_type": {
                        "type": "string",
                        "description": "task | incident | invoice | document",
                    },
                    "entity_id": {"type": "string"},
                    "days": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_event_history",
            "description": (
                "List durable operational events (TASK_COMPLETED, INCIDENT_CREATED, …). "
                "Survives server restart. Lower priority than live get_current_* state."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "event_type": {"type": "string"},
                    "entity_type": {"type": "string"},
                    "entity_id": {"type": "string"},
                    "q": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_entity_history",
            "description": (
                "Canonical entity timeline: current DB state + chronological audit events. "
                "REQUIRED for 'What happened to X?', 'Who changed/reassigned X?', "
                "'When was X completed?'. Never answer from conversation memory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_type": {"type": "string", "description": "task, incident, invoice, document"},
                    "entity_id": {"type": "string"},
                    "q": {"type": "string", "description": "Title search e.g. 'Maxime photos'"},
                    "limit": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_entity_state",
            "description": (
                "Current DB state ONLY (no history). Use for 'What is the status of X?' — "
                "not for 'what happened' questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_type": {"type": "string"},
                    "entity_id": {"type": "string"},
                    "q": {"type": "string"},
                },
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
                "Create NEW Google Calendar meeting(s) or reminders only — never for updates/reschedules. "
                "For 'change/move/update meeting' use list_calendar_events then update_calendar_event. "
                "Supports batch via events[] for multiple NEW meetings. "
                "Use meeting_kind=FOH|KITCHEN|MANAGER for department meetings "
                "(Front of House / Kitchen / Manager). "
                "Requires Google Calendar connected in Settings. Syncs WhatsApp + Dashboard."
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
                    "meeting_kind": {
                        "type": "string",
                        "description": "FOH, KITCHEN, or MANAGER for department meetings.",
                    },
                    "events": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_meetings",
            "description": (
                "Unified agenda: Google Calendar meetings + personal/compliance reminders. "
                "Use for 'what's on my calendar', department filters (meeting_kind=FOH|KITCHEN|MANAGER), "
                "and before confirming attendance. Same events as Dashboard + WhatsApp."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "q": {"type": "string"},
                    "meeting_kind": {"type": "string"},
                    "days": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_calendar_events",
            "description": (
                "Search or list Google Calendar meetings (keyword, person, location). "
                "Also merges personal reminders for parity. "
                "Call BEFORE update/delete/reschedule. Use when the manager asks what's on "
                "their calendar if [MANAGER SCHEDULE] is missing. Returns event_id per match."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "q": {
                        "type": "string",
                        "description": "Keywords: person name, meeting title, or location fragment.",
                    },
                    "days": {"type": "integer"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_reminders",
            "description": "List pending personal/daily/task/compliance reminders for this manager.",
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "q": {"type": "string"},
                    "status": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_reminder",
            "description": "Cancel a pending personal reminder by id or title keyword.",
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "reminder_id": {"type": "string"},
                    "q": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sync_compliance_reminder",
            "description": (
                "Ensure insurance/compliance document expiry has a Dashboard + WhatsApp reminder. "
                "Use after setting expiry or when asked to remind about insurance/compliance expiry."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "document_id": {"type": "string"},
                    "q": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_calendar_event",
            "description": (
                "Update/reschedule an EXISTING Google Calendar meeting — patch location, time, title, "
                "or notes. Requires event_id from list_calendar_events OR q when exactly one match. "
                "NEVER use create_calendar_event for updates (that duplicates meetings)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "event_id": {"type": "string", "description": "Google Calendar event id from list_calendar_events."},
                    "q": {
                        "type": "string",
                        "description": "Search keywords if event_id unknown (only when one match).",
                    },
                    "title": {"type": "string"},
                    "location": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_calendar_event",
            "description": (
                "Remove/cancel an EXISTING Google Calendar meeting. Requires event_id from "
                "list_calendar_events OR q when exactly one match. NEVER use create_calendar_event "
                "to remove meetings. Also cancels the linked WhatsApp reminder."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "event_id": {"type": "string"},
                    "q": {"type": "string"},
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
                "Create a WhatsApp personal reminder (one-shot or daily/weekly). "
                "Also appears on Dashboard Meetings & Reminders. "
                "Use for 'remind me', daily reminders, task reminders. "
                "For insurance/compliance expiry prefer sync_compliance_reminder."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "title": {"type": "string"},
                    "due_at": {"type": "string"},
                    "body": {"type": "string"},
                    "recurrence": {"type": "string"},
                    "reminder_kind": {
                        "type": "string",
                        "description": "task | daily | insurance | compliance",
                    },
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
                "List invoices (default OPEN). Returns invoice id — KEEP those ids for "
                "follow-ups (assign_invoice, mark_invoice_paid). Use for 'finance? rien à payer?'."
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
            "name": "search_operational_records",
            "description": (
                "Look up status of incidents, staff requests, tasks, or invoices by keywords or ref. "
                "Use when the user asks about status, progress, or whether something was fixed/repaired/approved "
                "('Has the computer screen been repaired?', 'status of my maintenance request'). "
                "Pass q with the main subject keywords. Reply with message_for_user from the tool — never tell "
                "the user to ask their manager."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "q": {
                        "type": "string",
                        "description": "Keywords or ref (e.g. 'computer screen', 'fridge', '7FFC0D68').",
                    },
                },
                "required": ["restaurant_id", "q"],
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
                "PayGuard payment approval lifecycle: list pending, start, approve, reject, "
                "or read policy. Use when invoice exceeds threshold; supports multi-step ladders."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "action": {
                        "type": "string",
                        "enum": ["list", "start", "approve", "reject", "request_info", "get_policy", "policy"],
                    },
                    "approval_id": {"type": "string"},
                    "invoice_id": {"type": "string"},
                    "vendor": {"type": "string"},
                    "reason": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_invoice",
            "description": (
                "Get one invoice live state: lifecycle_status, approval_status, amount, "
                "supplier, establishment, has_payment_proof. Prefer over stale list snapshots."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "invoice_id": {"type": "string"},
                    "vendor": {"type": "string"},
                    "invoice_number": {"type": "string"},
                    "q": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_invoice_approval",
            "description": (
                "CHECK AMOUNT then DETERMINE APPROVAL tier for an invoice "
                "(below/above threshold, multi-approver ladder)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "invoice_id": {"type": "string"},
                    "vendor": {"type": "string"},
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
                "Manager→staff broadcast via app + WhatsApp for TEAM-WIDE messages only "
                "(e.g. 'tell everyone we're closed tomorrow'). "
                "NEVER use for one person — use create_dashboard_task with assignee_name instead. "
                "Requires audience 'all' OR audience.staff_ids / roles / departments / tags."
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
            "name": "list_compliance_documents",
            "description": (
                "List compliance documents (insurance, hygiene, registration, fire safety) "
                "with expiry dates and urgency. Prefer [TENANT SNAPSHOT] when present."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "expiring_within_days": {"type": "integer"},
                    "attention_only": {"type": "boolean"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_compliance_document",
            "description": (
                "Set or change a compliance document expiry date, title, or reminder window. "
                "Use id from [TENANT SNAPSHOT] or list_compliance_documents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "id": {"type": "string"},
                    "document_id": {"type": "string"},
                    "title": {"type": "string"},
                    "expires_at": {"type": "string"},
                    "expiry_date": {"type": "string"},
                    "due_date": {"type": "string"},
                    "remind_days_before": {"type": "integer"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "seed_compliance_documents",
            "description": (
                "Create the suggested starter compliance document set for this workspace "
                "(registration, insurance, fire extinguisher, hygiene, health permit)."
            ),
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
            "name": "parse_photo",
            "description": (
                "Classify a photo and optionally auto-create records. Categories include "
                "invoice_or_receipt, task_or_app_screenshot (NOT an incident), equipment, "
                "incident, id_or_certification (insurance/permits → compliance doc when manager "
                "asks for renewal reminders). Always pass document_id from [ATTACHED DOCUMENTS] "
                "when present and note = manager caption (e.g. 'remind me 2 weeks before expiry'). "
                "For invoice photos with 'pay / garde en finance', call this to extract fields "
                "then use record_invoice through the control plane — extraction never creates records."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "document_id": {"type": "string"},
                    "media_url": {"type": "string"},
                    "image_url": {"type": "string"},
                    "image_base64": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "parse_document",
            "description": (
                "Read a PDF / Word / Excel / CSV document for extraction/classification. "
                "Pass document_id from [ATTACHED DOCUMENTS] and note = caption. "
                "Does NOT create invoices, compliance records, or process templates — "
                "returns structured preview only. Use record_invoice or explicit import workflows for mutations."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "document_id": {"type": "string"},
                    "media_url": {"type": "string"},
                    "document_url": {"type": "string"},
                    "document_base64": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_invoice_paid",
            "description": (
                "Mark a supplier invoice as paid. Pass invoice_id or vendor + invoice_number."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "invoice_id": {"type": "string"},
                    "vendor": {"type": "string"},
                    "vendor_name": {"type": "string"},
                    "invoice_number": {"type": "string"},
                    "method": {"type": "string"},
                    "reference": {"type": "string"},
                    "paid_on": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_invoice_timeline",
            "description": (
                "Full LIVE chronological history for one invoice — OCR, approvals, rejections, "
                "payments, proof uploads. REQUIRED for 'What happened to the invoice from ABC Foods?'. "
                "Never answer from a stale list — this returns current lifecycle_status + audit."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "invoice_id": {"type": "string"},
                    "vendor": {"type": "string"},
                    "vendor_name": {"type": "string"},
                    "invoice_number": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "attach_invoice_proof",
            "description": (
                "Attach proof of payment (bank receipt, transfer confirmation) to an invoice. "
                "Optionally mark_paid=true when uploading proof after payment."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "invoice_id": {"type": "string"},
                    "vendor": {"type": "string"},
                    "invoice_number": {"type": "string"},
                    "proof_url": {"type": "string"},
                    "mark_paid": {"type": "boolean"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "return_invoice",
            "description": (
                "Return an invoice for correction or request missing information before approval."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "invoice_id": {"type": "string"},
                    "vendor": {"type": "string"},
                    "invoice_number": {"type": "string"},
                    "reason": {"type": "string"},
                    "returned_reason": {"type": "string"},
                },
                "required": ["restaurant_id", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assign_invoice",
            "description": (
                "Transfer/hand open invoice(s) to a staff member for payment follow-up "
                "(e.g. 'transfère-les à Driss Wahabi'). Pass invoice_ids from the last "
                "list_invoices result, or all_open=true, plus staff_name/assignee_id. "
                "Creates FINANCE tasks on Operations Live and WhatsApps the assignee."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "staff_name": {"type": "string"},
                    "assignee_id": {"type": "string"},
                    "invoice_id": {"type": "string"},
                    "invoice_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "all_open": {"type": "boolean"},
                    "vendor": {"type": "string"},
                    "invoice_number": {"type": "string"},
                    "notify_whatsapp": {"type": "boolean"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tenant_documents",
            "description": (
                "List Miya uploads (PDFs, photos, certs) with structured fields "
                "(vendor, amount, expiry). Supports q and since=yesterday."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "limit": {"type": "integer"},
                    "q": {"type": "string"},
                },
                "required": ["restaurant_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_tenant_document",
            "description": (
                "Get structured fields + summary + extracted text for one uploaded document. "
                "Prefer structured vendor/amount/expiry over raw OCR."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "document_id": {"type": "string"},
                    "id": {"type": "string"},
                },
                "required": ["restaurant_id"],
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
    "list_incidents": ("GET", "/api/staff/agent/incidents/"),
    "search_operational_records": ("GET", "/api/staff/agent/records/search/"),
    "close_incident": ("POST", "/api/staff/agent/incidents/close/"),
    "request_time_off": ("POST", "/api/scheduling/agent/time-off/request/"),
    "create_dashboard_task": ("POST", "/api/dashboard/agent/tasks/create/"),
    "list_dashboard_widgets": ("POST", "/api/dashboard/agent/widgets/list/"),
    "list_dashboard_tasks": ("POST", "/api/dashboard/agent/tasks/list/"),
    "get_dashboard_task": ("POST", "/api/dashboard/agent/tasks/list/"),
    "list_operations_live": ("POST", "/api/dashboard/agent/operations-live/"),
    "cross_location_report": ("GET", "/api/dashboard/agent/cross-location-report/"),
    "location_detail": ("GET", "/api/dashboard/agent/location-detail/"),
    "notify_manager_urgent": ("POST", "/api/dashboard/agent/operations-live/notify/"),
    "update_dashboard_task_status": ("POST", "/api/dashboard/agent/tasks/status/"),
    "reassign_dashboard_task": ("POST", "/api/dashboard/agent/tasks/reassign/"),
    "update_dashboard_task": ("POST", "/api/dashboard/agent/tasks/update/"),
    "create_calendar_event": ("POST", "/api/dashboard/agent/calendar-events/create/"),
    "list_calendar_events": ("GET", "/api/dashboard/agent/calendar-events/list/"),
    "update_calendar_event": ("POST", "/api/dashboard/agent/calendar-events/update/"),
    "delete_calendar_event": ("POST", "/api/dashboard/agent/calendar-events/delete/"),
    "create_personal_reminder": ("POST", "/api/scheduling/agent/personal-reminders/"),
    "list_invoices": ("POST", "/api/finance/agent/invoices/list/"),
    "assign_invoice": ("POST", "/api/finance/agent/invoices/assign/"),
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
    "list_compliance_documents": ("GET", "/api/payroll/agent/compliance-documents/"),
    "update_compliance_document": ("PATCH", "/api/payroll/agent/compliance-documents/"),
    "seed_compliance_documents": ("POST", "/api/payroll/agent/compliance-documents/seed/"),
    "parse_photo": ("POST", "/api/dashboard/agent/parse-photo/"),
    "parse_document": ("POST", "/api/dashboard/agent/parse-document/"),
    "mark_invoice_paid": ("POST", "/api/finance/agent/invoices/mark-paid/"),
    "get_invoice_timeline": ("POST", "/api/finance/agent/invoices/timeline/"),
    "attach_invoice_proof": ("POST", "/api/finance/agent/invoices/proof-of-payment/"),
    "return_invoice": ("POST", "/api/finance/agent/invoices/return/"),
    "list_tenant_documents": ("GET", "/api/dashboard/tenant-documents/"),
    "get_tenant_document": ("GET", "/api/dashboard/tenant-documents/"),
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
        "list_incidents",
        "search_operational_records",
        "list_calendar_events",
        "list_inventory",
        "sales_summary",
        "list_compliance_documents",
        "cross_location_report",
        "location_detail",
    }
)


def _api_base() -> str:
    base = (getattr(settings, "MIYA_AGENT_API_BASE", None) or "").strip()
    if base:
        return base.rstrip("/")
    return "http://127.0.0.1:8000"


from core.agent_auth import primary_agent_bearer_token, is_agent_bearer


def _auth_headers(access_token: str | None, session_context: dict[str, Any] | None = None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if access_token and not is_agent_bearer(access_token):
        headers["Authorization"] = f"Bearer {access_token}"
    else:
        agent_key = primary_agent_bearer_token()
        if agent_key:
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

    if name == "update_compliance_document":
        if not payload.get("id") and payload.get("document_id"):
            payload["id"] = payload["document_id"]
        payload.setdefault("action", "update")

    if name == "seed_compliance_documents":
        payload.setdefault("action", "seed")

    if name == "create_dashboard_task":
        # WhatsApp / dashboard sender is the requester (From), not necessarily
        # the assignee (To). Keep dedicated requester fields so assignee
        # resolution does not steal the session user_id.
        if uid:
            payload.setdefault("requester_id", uid)
            payload.setdefault("sender_user_id", uid)
            payload.setdefault("created_by_id", uid)
        if phone:
            payload.setdefault("sender_phone", phone)
            payload.setdefault("requester_phone", phone)
        channel = str(session_context.get("channel") or payload.get("channel") or "").strip()
        if channel:
            payload.setdefault("channel", channel)
        source_bits = [
            payload.get("source_text"),
            payload.get("sourceText"),
            payload.get("user_message"),
            payload.get("userMessage"),
            payload.get("context"),
            payload.get("conversation"),
            session_context.get("last_user_message"),
        ]
        merged_source = " ".join(str(b).strip() for b in source_bits if b and str(b).strip())
        if merged_source:
            payload["source_text"] = merged_source
        widget_hint = str(payload.get("widget_title") or payload.get("widgetTitle") or "").strip()
        if widget_hint and widget_hint.lower() not in (payload.get("source_text") or "").lower():
            payload["source_text"] = f"{payload.get('source_text', '')} {widget_hint}".strip()
        if not payload.get("custom_widget_id") and payload.get("widget_id"):
            payload["custom_widget_id"] = payload["widget_id"]

    return payload


def execute_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    access_token: str | None,
    session_context: dict[str, Any],
    user=None,
) -> dict[str, Any]:
    if name == "dashboard_widgets":
        name = "list_dashboard_widgets"

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

    staff_self_task = name == "create_dashboard_task"
    if user is not None and name not in allowed_tools_for_user(user, restaurant=tenant_rest):
        if not staff_self_task:
            return {
                "success": False,
                "error": "You don't have permission for this action on Mizan.",
                "required_rbac": True,
            }

    if name in ("parse_photo", "parse_document"):
        from miya.services.media_tools import dispatch_parse_document, dispatch_parse_photo

        hdrs = _auth_headers(access_token, session_context)
        agent_key = primary_agent_bearer_token()
        if agent_key:
            hdrs = {**hdrs, "Authorization": f"Bearer {agent_key}"}
        dispatch_fn = dispatch_parse_photo if name == "parse_photo" else dispatch_parse_document
        status_code, body = dispatch_fn(dict(arguments or {}), session_context, headers=hdrs)
        if isinstance(body, dict):
            body.setdefault("success", 200 <= status_code < 300)
        return body if isinstance(body, dict) else {"success": False, "raw": body}

    from miya.services.ops import CANONICAL_TOOL_NAMES

    is_canonical = name in CANONICAL_TOOL_NAMES
    route = _ROUTE_MAP.get(name)
    if not route and not is_canonical:
        return {"success": False, "error": f"Unknown tool: {name}"}

    method, path = route if route else ("POST", "/canonical/ops/")
    if name in _GET_METHOD_TOOLS:
        method = "GET"
    if name == "category_routing" and str((arguments or {}).get("action") or "get").lower() == "get":
        method = "GET"
    payload = dict(arguments or {})

    # Wrong tool choice: "tell Adama to …" must use create_dashboard_task with
    # structured assignee_name — NEVER rewrite by parsing announcement NL text.
    if name == "send_announcement":
        from miya.services.staff_delegation import (
            audience_has_specific_targets,
            audience_is_broadcast,
        )

        audience = payload.get("audience")
        if not audience_is_broadcast(audience) and not audience_has_specific_targets(audience):
            return {
                "success": False,
                "code": "structured_tool_required",
                "error": "structured_tool_required",
                "message_for_user": (
                    "I need a staff name to assign that as a task. "
                    "Tell me who, and I'll create a dashboard task."
                ),
                "miya_directive": (
                    "Do NOT encode the action in natural language. "
                    "Call create_dashboard_task with structured fields: "
                    "assignee_name (or assignee_id), title, description. "
                    "Never parse your own reply to decide the assignee."
                ),
                "verified": False,
            }

    staff_self_task = name == "create_dashboard_task"

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

    if name == "get_incident_photo":
        if not payload.get("phone") and phone:
            payload["phone"] = phone

    if name == "show_document":
        if not payload.get("phone") and phone:
            payload["phone"] = phone

    if name == "request_time_off" and not payload.get("staff_id"):
        payload["staff_id"] = uid

    if name == "create_dashboard_task" and payload.get("assign_to_self") and not payload.get("assignee_id"):
        payload["assignee_id"] = uid

    if name == "create_dashboard_task" and user is not None and staff_self_task:
        from accounts.rbac_enforce import miya_has_full_tenant_access, user_can_action

        if not miya_has_full_tenant_access(user, tenant_rest) and not user_can_action(
            user, "manage_widgets", restaurant=tenant_rest
        ):
            payload["assign_to_self"] = True
            if uid:
                payload["assignee_id"] = uid
            payload.setdefault("notify_whatsapp", False)

    if name in ("list_dashboard_widgets", "dashboard_widgets") and not payload.get("user_id"):
        payload["user_id"] = uid

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

    if name == "return_invoice" and payload.get("returned_reason") and not payload.get("reason"):
        payload["reason"] = payload["returned_reason"]

    if name == "get_invoice_timeline" and payload.get("vendor") and not payload.get("vendor_name"):
        payload["vendor_name"] = payload["vendor"]

    payload = _enrich_agent_payload(name, payload, session_context)

    if name == "create_dashboard_task" and user is not None:
        from miya.services.manager_reminder_intent import (
            is_manager_role,
            looks_like_manager_reminder_intent,
            manager_self_task_blocked_message,
        )

        if is_manager_role(user):
            uid = str(session_context.get("user_id") or getattr(user, "id", "") or "")
            assignee_id = str(
                payload.get("assignee_id")
                or payload.get("assignee_user_id")
                or payload.get("user_id")
                or ""
            ).strip()
            assign_to_self = payload.get("assign_to_self") in (True, "true", "1", 1)
            has_staff_target = bool(
                payload.get("assignee_name")
                or payload.get("staff_name")
                or payload.get("assign_to_category")
                or (assignee_id and uid and assignee_id != uid)
            )
            combined = " ".join(
                str(payload.get(k) or "")
                for k in ("title", "description", "source_text", "user_message")
            ).strip()
            if assign_to_self or (
                not has_staff_target
                and (looks_like_manager_reminder_intent(combined) or not combined)
            ):
                from core.i18n import get_effective_language, tr

                lang = get_effective_language(user=user, restaurant=tenant_rest)
                if looks_like_manager_reminder_intent(combined) or assign_to_self:
                    msg = tr("miya.use_reminder_not_task", lang)
                else:
                    msg = manager_self_task_blocked_message(language=lang)
                return {
                    "success": False,
                    "code": "MANAGER_SELF_TASK_BLOCKED",
                    "error": "manager_cannot_self_assign_task",
                    "miya_directive": (
                        "Do NOT use create_dashboard_task for the manager's own reminders. "
                        "Call create_personal_reminder, create_calendar_event, or "
                        "update_compliance_document / parse_document instead."
                    ),
                    "message_for_user": msg,
                }

    # Resolve pronouns / missing ids from the turn-local working set
    # (list_invoices → "transfère-les", list tasks → "cancel it").
    try:
        from miya.services.working_set import apply_working_set_to_args

        payload = apply_working_set_to_args(
            name,
            payload,
            restaurant_id=str(rid or payload.get("restaurant_id") or "") or None,
            user_id=str(uid or "") or None,
        )
    except Exception:
        logger.exception("working_set apply failed for %s", name)

    # Phase 1: structured intelligence actions (verify + audit + events)
    from miya.services.intelligence.actions import execute_structured_action, is_structured_action
    from miya.services.intelligence.context_engine import execution_context_from_session
    from miya.services.ops import build_ops_context, dispatch_canonical_tool

    if is_structured_action(name) or is_canonical:
        ops_ctx = build_ops_context(
            user=user,
            restaurant=tenant_rest,
            session_context=session_context,
        )
        if ops_ctx is None:
            return {
                "success": False,
                "code": "restaurant_required",
                "error": "restaurant_required",
                "message_for_user": "I couldn't determine which establishment this is for.",
                "miya_directive": (
                    "Do NOT tell the user the action succeeded. "
                    "Ask which location/workspace if needed."
                ),
                "verified": False,
            }

        ops_result = None
        if is_structured_action(name):
            exec_pub = {}
            try:
                ectx = execution_context_from_session(
                    user=user,
                    session_context=session_context,
                    restaurant=tenant_rest,
                )
                if ectx:
                    exec_pub = ectx.to_public_dict()
            except Exception:
                exec_pub = {
                    "message_id": (session_context or {}).get("_pipeline_message_id"),
                    "conversation_id": (session_context or {}).get("_pipeline_conversation_id"),
                    "user_id": (session_context or {}).get("user_id"),
                    "organization_id": (session_context or {}).get("restaurant_id"),
                    "establishment_id": (session_context or {}).get("location_id"),
                    "channel": (session_context or {}).get("channel"),
                }
            # Ensure tool-call operation id flows into the action layer
            if payload.get("_operation_id"):
                payload = dict(payload)
            ops_result = execute_structured_action(
                name,
                payload,
                ctx=ops_ctx,
                execution_context=exec_pub,
                intent=str(payload.get("intent") or name),
            )
        else:
            ops_result = dispatch_canonical_tool(name, payload, ctx=ops_ctx)

        if ops_result is not None:
            from miya.services.intelligence.mutation_pipeline import (
                ensure_ops_mutation_verified,
                enforce_mutation_tool_response,
            )

            ops_result = ensure_ops_mutation_verified(name, ops_result)
            body = ops_result.as_tool_response()
            body = enforce_mutation_tool_response(name, body)
            try:
                from miya.services.reply_format import sanitize_tool_payload_for_llm

                body = sanitize_tool_payload_for_llm(body)
            except Exception:
                pass
            # Persist establishment switch into the live session for follow-up tools
            if ops_result.success and isinstance(body, dict) and isinstance(session_context, dict):
                patch = body.get("session_patch")
                if not isinstance(patch, dict) and isinstance(body.get("data"), dict):
                    patch = body["data"].get("session_patch")
                if isinstance(patch, dict):
                    for key in ("location_id", "location_name"):
                        if patch.get(key):
                            session_context[key] = patch[key]
                    if ops_ctx.location_id:
                        session_context["location_id"] = ops_ctx.location_id
                    if ops_ctx.location_name:
                        session_context["location_name"] = ops_ctx.location_name
                elif ops_ctx.location_id and name in (
                    "set_establishment_context",
                    "switch_establishment",
                ):
                    session_context["location_id"] = ops_ctx.location_id
                    if ops_ctx.location_name:
                        session_context["location_name"] = ops_ctx.location_name
            if ops_result.success and isinstance(body, dict):
                try:
                    from miya.services.working_set import extract_list_entities, remember_entities

                    kind, entities = extract_list_entities(name, body)
                    if kind and entities:
                        remember_entities(
                            restaurant_id=str(rid or payload.get("restaurant_id") or "") or None,
                            user_id=str(uid or "") or None,
                            kind=kind,
                            entities=entities,
                        )
                except Exception:
                    logger.exception("working_set remember failed for canonical %s", name)
            return body
        if not route:
            return {"success": False, "error": f"Unknown tool: {name}"}

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
        audience = payload.get("audience")
        from miya.services.staff_delegation import audience_has_specific_targets, audience_is_broadcast

        if not audience_is_broadcast(audience) and not audience_has_specific_targets(audience):
            payload["broadcast_all"] = False
        elif audience_is_broadcast(audience):
            payload["broadcast_all"] = True
            payload["audience"] = "all"

    url = f"{_api_base()}{path}"
    headers = _auth_headers(access_token, session_context)
    try:
        from .tool_dispatch import dispatch_agent_request, should_dispatch_in_process

        if should_dispatch_in_process(_api_base()):
            agent_key = primary_agent_bearer_token()
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
        return {"success": False, "error": str(exc), "verified": False}

    from miya.services.intelligence.mutation_pipeline import finalize_legacy_tool_response

    result = finalize_legacy_tool_response(name, status_code=status_code, body=body)

    # Remember listed entities for the next short reply / pronoun turn.
    if result.get("success") and isinstance(body, dict):
        try:
            from miya.services.working_set import extract_list_entities, remember_entities

            kind, entities = extract_list_entities(name, body)
            if kind and entities:
                remember_entities(
                    restaurant_id=str(rid or payload.get("restaurant_id") or "") or None,
                    user_id=str(uid or "") or None,
                    kind=kind,
                    entities=entities,
                )
        except Exception:
            logger.exception("working_set remember failed for %s", name)

    return result


def serialize_tool_result(result: dict[str, Any]) -> str:
    return json.dumps(result, default=str)[:8000]
