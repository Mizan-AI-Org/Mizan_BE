"""Rich automation library metadata — triggers, actions, templates."""

from __future__ import annotations

TRIGGER_CATALOG: list[dict] = [
    {
        "id": "new_message_received",
        "category": "whatsapp",
        "icon": "message",
        "config_fields": [],
    },
    {
        "id": "first_message_from_contact",
        "category": "whatsapp",
        "icon": "user-plus",
        "config_fields": [],
    },
    {
        "id": "keyword_match",
        "category": "whatsapp",
        "icon": "search",
        "config_fields": ["keywords"],
    },
    {
        "id": "new_contact_created",
        "category": "whatsapp",
        "icon": "contact",
        "config_fields": [],
    },
    {
        "id": "tag_added",
        "category": "contact",
        "icon": "tag",
        "config_fields": ["tag"],
    },
    {
        "id": "time_based",
        "category": "scheduling",
        "icon": "clock",
        "config_fields": ["off_hours", "inactive_hours"],
    },
]

ACTION_CATALOG: list[dict] = [
    {"id": "send_message", "category": "messaging", "icon": "message-square"},
    {"id": "send_template", "category": "messaging", "icon": "file-text"},
    {"id": "add_tag", "category": "contact", "icon": "tag"},
    {"id": "remove_tag", "category": "contact", "icon": "tag-off"},
    {"id": "assign_conversation", "category": "contact", "icon": "user-check"},
    {"id": "update_contact_field", "category": "contact", "icon": "edit"},
    {"id": "create_task", "category": "operations", "icon": "check-square"},
    {"id": "create_staff_request", "category": "operations", "icon": "inbox"},
    {"id": "wait", "category": "flow", "icon": "timer"},
    {"id": "condition", "category": "flow", "icon": "git-branch"},
    {"id": "send_webhook", "category": "integrations", "icon": "webhook"},
    {"id": "close_conversation", "category": "messaging", "icon": "check-circle"},
]

TEMPLATE_LIBRARY: list[dict] = [
    {
        "id": "welcome_message",
        "category": "getting_started",
        "tags": ["whatsapp", "onboarding"],
        "difficulty": "easy",
        "trigger": {"type": "first_message_from_contact", "config": {}},
        "steps": [
            {
                "type": "send_message",
                "config": {
                    "text": (
                        "Hi! Welcome to our team on WhatsApp. "
                        "I'm Miya — ask me about shifts, tasks, or how to get started."
                    ),
                },
            }
        ],
    },
    {
        "id": "out_of_office",
        "category": "customer_care",
        "tags": ["hours", "auto-reply"],
        "difficulty": "easy",
        "trigger": {"type": "time_based", "config": {"off_hours": True}},
        "steps": [
            {
                "type": "send_message",
                "config": {
                    "text": (
                        "Thanks for your message. We're outside business hours right now "
                        "and will get back to you as soon as we're open."
                    ),
                },
            }
        ],
    },
    {
        "id": "lead_qualifier",
        "category": "customer_care",
        "tags": ["sales", "inbound"],
        "difficulty": "medium",
        "trigger": {"type": "new_message_received", "config": {}},
        "steps": [
            {
                "type": "send_message",
                "config": {
                    "text": (
                        "Thanks for reaching out! To help you faster, please share: "
                        "1) Your name  2) What you need  3) Best time to call."
                    ),
                },
            },
            {
                "type": "create_staff_request",
                "config": {
                    "category": "OPERATIONS",
                    "subject": "New inbound lead from WhatsApp",
                    "description": "Auto-routed from WhatsApp inbound message.",
                },
            },
        ],
    },
    {
        "id": "sales_process",
        "category": "customer_care",
        "tags": ["sales", "inquiry", "follow-up"],
        "difficulty": "medium",
        "trigger": {
            "type": "keyword_match",
            "config": {
                "keywords": [
                    "quote",
                    "price",
                    "buy",
                    "order",
                    "sales",
                    "inquiry",
                    "interested",
                    "purchase",
                ],
            },
        },
        "steps": [
            {"type": "add_tag", "config": {"tag": "SALES_INQUIRY"}},
            {
                "type": "send_message",
                "config": {
                    "text": (
                        "Thank you for your inquiry! We'll get back to you shortly. "
                        "A team member will follow up soon."
                    ),
                },
            },
            {
                "type": "create_staff_request",
                "config": {
                    "category": "OPERATIONS",
                    "subject": "New sales inquiry from WhatsApp",
                    "description": "Auto-routed from sales keyword on WhatsApp.",
                },
            },
        ],
    },
    {
        "id": "follow_up_reminder",
        "category": "customer_care",
        "tags": ["follow-up", "retention"],
        "difficulty": "easy",
        "trigger": {"type": "time_based", "config": {"inactive_hours": 24}},
        "steps": [
            {
                "type": "send_message",
                "config": {"text": "Quick follow-up — did you still need help with this?"},
            }
        ],
    },
    {
        "id": "keyword_vip",
        "category": "customer_care",
        "tags": ["vip", "priority"],
        "difficulty": "easy",
        "trigger": {"type": "keyword_match", "config": {"keywords": ["VIP", "vip"]}},
        "steps": [
            {"type": "add_tag", "config": {"tag": "VIP"}},
            {
                "type": "send_message",
                "config": {"text": "Thanks — we've flagged you as VIP priority."},
            },
        ],
    },
    {
        "id": "shift_clock_in_nudge",
        "category": "staff_ops",
        "tags": ["scheduling", "attendance"],
        "difficulty": "easy",
        "trigger": {"type": "keyword_match", "config": {"keywords": ["CLOCK IN", "clock in"]}},
        "steps": [
            {
                "type": "send_message",
                "config": {
                    "text": "Reply CLOCK IN when you arrive, or ask Miya for today's shift details.",
                },
            }
        ],
    },
    {
        "id": "missed_shift_escalation",
        "category": "staff_ops",
        "tags": ["scheduling", "escalation"],
        "difficulty": "medium",
        "trigger": {"type": "keyword_match", "config": {"keywords": ["NO SHOW", "no-show"]}},
        "steps": [
            {
                "type": "create_task",
                "config": {
                    "title": "Review missed shift / no-show",
                    "priority": "HIGH",
                    "description": "Reported via WhatsApp automation.",
                },
            },
            {
                "type": "send_message",
                "config": {
                    "text": "Noted — a manager will review this shift issue shortly.",
                },
            },
        ],
    },
    {
        "id": "incident_acknowledgment",
        "category": "staff_ops",
        "tags": ["safety", "incidents"],
        "difficulty": "medium",
        "trigger": {"type": "keyword_match", "config": {"keywords": ["INCIDENT", "incident"]}},
        "steps": [
            {
                "type": "send_message",
                "config": {
                    "text": (
                        "Incident received. Please share location, what happened, "
                        "and if anyone is hurt. A manager is being notified."
                    ),
                },
            },
            {
                "type": "create_staff_request",
                "config": {
                    "category": "SAFETY",
                    "subject": "WhatsApp incident report",
                },
            },
        ],
    },
    {
        "id": "reservation_inquiry",
        "category": "customer_care",
        "tags": ["reservations", "guests"],
        "difficulty": "medium",
        "trigger": {"type": "keyword_match", "config": {"keywords": ["RESERVE", "reservation", "book"]}},
        "steps": [
            {
                "type": "send_message",
                "config": {
                    "text": (
                        "Happy to help with a reservation! Please send: "
                        "date, time, party size, and name for the booking."
                    ),
                },
            },
            {
                "type": "create_task",
                "config": {
                    "title": "Reservation inquiry from WhatsApp",
                    "priority": "MEDIUM",
                },
            },
        ],
    },
    {
        "id": "menu_hours_reply",
        "category": "customer_care",
        "tags": ["menu", "hours"],
        "difficulty": "easy",
        "trigger": {"type": "keyword_match", "config": {"keywords": ["MENU", "HOURS", "hours", "menu"]}},
        "steps": [
            {
                "type": "send_message",
                "config": {
                    "text": (
                        "Our latest menu and hours are on our website. "
                        "Tell us what you're craving and we'll point you to the right items!"
                    ),
                },
            }
        ],
    },
    {
        "id": "staff_onboarding",
        "category": "staff_ops",
        "tags": ["onboarding", "hr"],
        "difficulty": "easy",
        "trigger": {"type": "new_contact_created", "config": {}},
        "steps": [
            {
                "type": "send_message",
                "config": {
                    "text": (
                        "Welcome to the team on WhatsApp! Save this number. "
                        "Ask Miya about shifts, tasks, and how to clock in."
                    ),
                },
            },
            {"type": "add_tag", "config": {"tag": "NEW_STAFF"}},
        ],
    },
    {
        "id": "complaint_triage",
        "category": "customer_care",
        "tags": ["complaints", "support"],
        "difficulty": "medium",
        "trigger": {"type": "keyword_match", "config": {"keywords": ["COMPLAINT", "unhappy", "refund"]}},
        "steps": [
            {
                "type": "send_message",
                "config": {
                    "text": (
                        "Sorry to hear that. We want to make it right — "
                        "please share your visit date and what went wrong."
                    ),
                },
            },
            {
                "type": "create_staff_request",
                "config": {
                    "category": "OPERATIONS",
                    "subject": "Guest complaint via WhatsApp",
                    "description": "Auto-routed from complaint keyword.",
                },
            },
            {"type": "add_tag", "config": {"tag": "COMPLAINT"}},
        ],
    },
    {
        "id": "after_hours_escalation",
        "category": "advanced",
        "tags": ["after-hours", "urgent"],
        "difficulty": "advanced",
        "trigger": {"type": "time_based", "config": {"off_hours": True}},
        "steps": [
            {
                "type": "send_message",
                "config": {
                    "text": "We're closed now. For urgent issues text URGENT and a manager will be alerted.",
                },
            },
            {
                "type": "create_staff_request",
                "config": {
                    "category": "OPERATIONS",
                    "subject": "After-hours WhatsApp message",
                },
            },
        ],
    },
    {
        "id": "feedback_collector",
        "category": "customer_care",
        "tags": ["feedback", "nps"],
        "difficulty": "easy",
        "trigger": {"type": "keyword_match", "config": {"keywords": ["FEEDBACK", "feedback"]}},
        "steps": [
            {
                "type": "send_message",
                "config": {
                    "text": "Thanks! Rate your experience 1–5 (5 = excellent) and add any comments.",
                },
            }
        ],
    },
    {
        "id": "emergency_alert",
        "category": "staff_ops",
        "tags": ["safety", "urgent"],
        "difficulty": "advanced",
        "trigger": {"type": "keyword_match", "config": {"keywords": ["EMERGENCY", "emergency", "URGENT"]}},
        "steps": [
            {"type": "add_tag", "config": {"tag": "URGENT"}},
            {
                "type": "create_task",
                "config": {
                    "title": "URGENT WhatsApp alert",
                    "priority": "CRITICAL",
                },
            },
            {
                "type": "send_message",
                "config": {
                    "text": "Urgent message received. A manager is being notified now.",
                },
            },
        ],
    },
    {
        "id": "coverage_request",
        "category": "scheduling",
        "tags": ["coverage", "shifts"],
        "difficulty": "medium",
        "trigger": {"type": "keyword_match", "config": {"keywords": ["COVERAGE", "cover my shift"]}},
        "steps": [
            {
                "type": "create_staff_request",
                "config": {
                    "category": "SCHEDULING",
                    "subject": "Shift coverage request via WhatsApp",
                },
            },
            {
                "type": "send_message",
                "config": {
                    "text": "Coverage request logged. Your manager will follow up on available shifts.",
                },
            },
        ],
    },
    {
        "id": "invoice_follow_up",
        "category": "operations",
        "tags": ["finance", "accounts"],
        "difficulty": "medium",
        "trigger": {"type": "keyword_match", "config": {"keywords": ["INVOICE", "invoice", "payment"]}},
        "steps": [
            {
                "type": "create_task",
                "config": {
                    "title": "Invoice / payment inquiry",
                    "priority": "MEDIUM",
                },
            },
            {
                "type": "send_message",
                "config": {
                    "text": "Thanks — we've logged your invoice question. Finance will reply shortly.",
                },
            },
        ],
    },
    {
        "id": "miya_handoff",
        "category": "advanced",
        "tags": ["miya", "routing"],
        "difficulty": "advanced",
        "trigger": {"type": "keyword_match", "config": {"keywords": ["AGENT", "human", "manager"]}},
        "steps": [
            {
                "type": "send_message",
                "config": {
                    "text": "Connecting you with the team — a manager will respond shortly.",
                },
            },
            {"type": "close_conversation", "config": {}},
        ],
    },
    {
        "id": "daily_opening_message",
        "category": "scheduling",
        "tags": ["daily", "broadcast"],
        "difficulty": "easy",
        "trigger": {"type": "time_based", "config": {"daily_morning": True}},
        "steps": [
            {
                "type": "send_message",
                "config": {
                    "text": "Good morning team! Check today's tasks in Mizan and reply if you need help.",
                },
            }
        ],
    },
]

# Human-readable labels (English defaults; frontend i18n overrides by id)
TRIGGER_LABELS = {
    "new_message_received": "Any incoming WhatsApp message",
    "first_message_from_contact": "First message from a new contact on this number",
    "keyword_match": "Inbound message contains a keyword",
    "new_contact_created": "New staff/contact activated on WhatsApp",
    "tag_added": "Contact tag added (session context)",
    "time_based": "Scheduled time (cron-like, daily)",
}

ACTION_LABELS = {
    "send_message": "Send WhatsApp text reply",
    "send_template": "Send WhatsApp template message",
    "add_tag": "Add tag to contact session",
    "remove_tag": "Remove tag from contact session",
    "assign_conversation": "Assign conversation to a staff member",
    "update_contact_field": "Update session/contact note",
    "create_task": "Create a dashboard task for follow-up",
    "create_staff_request": "Create a staff inbox request",
    "wait": "Wait before next step",
    "condition": "If/else branch on keyword or tag",
    "send_webhook": "POST JSON to external URL",
    "close_conversation": "Mark session idle / close thread",
}

TEMPLATE_META = {
    tpl["id"]: {
        "name": tpl["id"].replace("_", " ").title(),
        "description": f"Pre-built workflow: {tpl['id'].replace('_', ' ')}.",
    }
    for tpl in TEMPLATE_LIBRARY
}

# Legacy exports
TRIGGER_TYPES = TRIGGER_LABELS
ACTION_TYPES = ACTION_LABELS

QUICK_START_TEMPLATES: list[dict] = []
for tpl in TEMPLATE_LIBRARY:
    meta = TEMPLATE_META.get(tpl["id"], {})
    QUICK_START_TEMPLATES.append(
        {
            "id": tpl["id"],
            "name": meta.get("name", tpl["id"]),
            "description": meta.get("description", ""),
            "category": tpl.get("category", "getting_started"),
            "tags": tpl.get("tags") or [],
            "difficulty": tpl.get("difficulty", "easy"),
            "step_count": len(tpl.get("steps") or []),
            "trigger": tpl["trigger"],
            "steps": tpl.get("steps") or [],
        }
    )

CATALOG_CATEGORIES = {
    "template": [
        {"id": "getting_started", "label": "Getting started"},
        {"id": "customer_care", "label": "Customer care"},
        {"id": "staff_ops", "label": "Staff & operations"},
        {"id": "scheduling", "label": "Scheduling"},
        {"id": "operations", "label": "Operations"},
        {"id": "advanced", "label": "Advanced"},
    ],
    "trigger": [
        {"id": "whatsapp", "label": "WhatsApp"},
        {"id": "contact", "label": "Contact"},
        {"id": "scheduling", "label": "Scheduling"},
    ],
    "action": [
        {"id": "messaging", "label": "Messaging"},
        {"id": "contact", "label": "Contact"},
        {"id": "operations", "label": "Operations"},
        {"id": "flow", "label": "Flow control"},
        {"id": "integrations", "label": "Integrations"},
    ],
}

VARIABLE_TOKENS = [
    {"token": "{{contact_phone}}", "description": "Sender phone number"},
    {"token": "{{message_text}}", "description": "Inbound message body"},
    {"token": "{{restaurant_name}}", "description": "Your business name"},
]
