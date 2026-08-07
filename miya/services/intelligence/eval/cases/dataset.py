"""Realistic Mizan operational eval scenarios — permanent regression dataset."""
from __future__ import annotations

from miya.services.intelligence.eval.types import (
    EvalCase,
    EvalCategory,
    EvalExpectation,
    EvalTier,
    WorldEntity,
)

# Shared world fixtures
DECORATION_TASK = WorldEntity(
    id="task-deco-001",
    kind="task",
    title="Decoration",
    status="IN_PROGRESS",
    location_id="loc-casa",
)
FREEZER_INCIDENT = WorldEntity(
    id="inc-freezer-001",
    kind="incident",
    title="Freezer malfunction",
    status="OPEN",
    location_id="loc-casa",
)
CHECKLIST_A = WorldEntity(
    id="task-chk-a",
    kind="task",
    title="FOH opening checklist",
    status="PENDING",
    location_id="loc-casa",
)
CHECKLIST_B = WorldEntity(
    id="task-chk-b",
    kind="task",
    title="Kitchen opening checklist",
    status="PENDING",
    location_id="loc-casa",
)

_SINGLE_LOC = {
    "location_id": "loc-casa",
    "location_name": "Casablanca",
    "available_locations": [{"id": "loc-casa", "name": "Casablanca"}],
}
_MULTI_LOC = {
    "location_id": None,
    "available_locations": [
        {"id": "loc-casa", "name": "Casablanca"},
        {"id": "loc-rabat", "name": "Rabat"},
    ],
}


EVAL_CASES: list[EvalCase] = [
    # ── TASKS ──────────────────────────────────────────────────────────────
    EvalCase(
        id="task-complete-decoration",
        category=EvalCategory.TASKS,
        input="Close the decoration task.",
        critical=True,
        world=[DECORATION_TASK],
        session=_SINGLE_LOC,
        expected=EvalExpectation(
            intent="COMPLETE",
            entity_type="task",
            entity_query="decoration",
            entity_id="task-deco-001",
            tool="complete_task",
            tool_args_contains={"task_id": "task-deco-001"},
            db_state={"status": "COMPLETED"},
            response_must_contain=["Decoration"],
            verified=True,
            require_mutation_tool=True,
            max_tool_calls=1,
        ),
    ),
    EvalCase(
        id="task-complete-pronoun-working-set",
        category=EvalCategory.TASKS,
        input="Complete it.",
        session={**_SINGLE_LOC, "working_set": {"tasks": ["task-deco-001"]}},
        world=[DECORATION_TASK],
        expected=EvalExpectation(
            intent="COMPLETE",
            entity_type="task",
            entity_id="task-deco-001",
            verified=None,
            require_mutation_tool=False,
            # MEDIUM confidence → CONFIRM before execute (acceptable)
        ),
    ),
    EvalCase(
        id="task-assign-to-ahmed",
        category=EvalCategory.TASKS,
        input="Assign the closing checklist to Ahmed.",
        world=[WorldEntity(id="task-close-1", kind="task", title="Closing checklist", status="PENDING")],
        session=_SINGLE_LOC,
        expected=EvalExpectation(
            intent="ASSIGN",
            entity_type="task",
            clarify=True,
            require_mutation_tool=False,
            response_must_contain=["task"],
        ),
    ),
    # ── INCIDENTS ──────────────────────────────────────────────────────────
    EvalCase(
        id="incident-report-freezer",
        category=EvalCategory.INCIDENTS,
        input="Report a broken freezer incident.",
        session=_SINGLE_LOC,
        expected=EvalExpectation(
            intent="CREATE",
            entity_type="incident",
            tool="create_incident",
            verified=True,
            require_mutation_tool=True,
        ),
    ),
    EvalCase(
        id="incident-route-maintenance",
        category=EvalCategory.INCIDENTS,
        input="Route the freezer incident to maintenance.",
        world=[FREEZER_INCIDENT],
        session=_SINGLE_LOC,
        tier=EvalTier.PLANNING,
        expected=EvalExpectation(
            intent="ROUTE",
            entity_type="incident",
            verified=None,
        ),
    ),
    EvalCase(
        id="incident-close-freezer",
        category=EvalCategory.INCIDENTS,
        input="Close the freezer incident.",
        world=[FREEZER_INCIDENT],
        session=_SINGLE_LOC,
        tier=EvalTier.PLANNING,
        expected=EvalExpectation(
            intent="COMPLETE",
            entity_type="incident",
            verified=None,
        ),
        notes="Close incident phrase maps to COMPLETE/incident (Phase 12 paraphrase).",
    ),
    # ── STAFF ──────────────────────────────────────────────────────────────
    EvalCase(
        id="staff-find-ahmed",
        category=EvalCategory.STAFF,
        input="Find staff named Ahmed.",
        session=_SINGLE_LOC,
        tier=EvalTier.PLANNING,
        expected=EvalExpectation(
            intent="QUERY",
            entity_type="staff",
            tool="find_staff",
            verified=None,
            require_mutation_tool=False,
        ),
    ),
    EvalCase(
        id="staff-who-is-sara",
        category=EvalCategory.STAFF,
        input="Who is Sara?",
        session=_SINGLE_LOC,
        tier=EvalTier.PLANNING,
        expected=EvalExpectation(
            intent="QUERY",
            entity_type="staff",
            tool="find_staff",
            verified=None,
        ),
    ),
    # ── ROUTING ────────────────────────────────────────────────────────────
    EvalCase(
        id="routing-send-to-hr",
        category=EvalCategory.ROUTING,
        input="Send this to HR.",
        session={**_SINGLE_LOC, "working_set": {"tasks": ["task-deco-001"]}},
        world=[DECORATION_TASK],
        expected=EvalExpectation(
            intent="ASSIGN",
            entity_type="category",
            tool="assign_task",
            verified=True,
        ),
    ),
    EvalCase(
        id="routing-escalate-maintenance",
        category=EvalCategory.ROUTING,
        input="Route the freezer incident to maintenance.",
        world=[FREEZER_INCIDENT],
        session=_SINGLE_LOC,
        tier=EvalTier.PLANNING,
        expected=EvalExpectation(
            intent="ROUTE",
            entity_type="incident",
            verified=None,
        ),
    ),
    # ── DOCUMENTS ──────────────────────────────────────────────────────────
    EvalCase(
        id="doc-show-insurance",
        category=EvalCategory.DOCUMENTS,
        input="Show me documents related to insurance.",
        session=_SINGLE_LOC,
        expected=EvalExpectation(
            intent="RETRIEVE",
            entity_type="document",
            entity_query="insurance",
            tool="retrieve_document",
            require_mutation_tool=False,
        ),
    ),
    EvalCase(
        id="doc-retrieve-safety-manual",
        category=EvalCategory.DOCUMENTS,
        input="Show me the safety manual document.",
        session=_SINGLE_LOC,
        tier=EvalTier.PLANNING,
        expected=EvalExpectation(
            intent="RETRIEVE",
            entity_type="document",
            tool="retrieve_document",
            verified=None,
        ),
    ),
    # ── OCR / MULTIMODAL ───────────────────────────────────────────────────
    EvalCase(
        id="ocr-report-this-image",
        category=EvalCategory.OCR,
        input="Report this.",
        session={
            **_SINGLE_LOC,
            "_multimodal": {
                "modalities": ["image"],
                "primary_kind": "image",
                "ocr_text": "Freezer temperature alarm — walk-in unit 3",
            },
        },
        expected=EvalExpectation(
            intent="CREATE",
            entity_type="incident",
            tool="create_incident",
            verified=True,
            require_mutation_tool=True,
        ),
    ),
    EvalCase(
        id="ocr-invoice-from-photo",
        category=EvalCategory.OCR,
        input="",
        session={
            **_SINGLE_LOC,
            "_multimodal": {
                "modalities": ["image"],
                "primary_kind": "invoice",
                "suggested_intent": "CREATE",
                "suggested_entity": "invoice",
                "ocr_text": "Acme Foods invoice #4421 total 1250.00 MAD",
                "attachments": [{"id": "att-invoice-1", "kind": "image"}],
            },
        },
        tier=EvalTier.PLANNING,
        expected=EvalExpectation(
            intent="CREATE",
            entity_type="invoice",
            tool="record_invoice",
            require_mutation_tool=False,
        ),
    ),
    # ── INVOICES ───────────────────────────────────────────────────────────
    EvalCase(
        id="invoice-record-acme",
        category=EvalCategory.INVOICES,
        input="Show invoices from Acme Foods.",
        session=_SINGLE_LOC,
        tier=EvalTier.PLANNING,
        expected=EvalExpectation(
            intent="QUERY",
            verified=None,
            require_mutation_tool=False,
        ),
    ),
    EvalCase(
        id="invoice-show-pending",
        category=EvalCategory.INVOICES,
        input="Show pending invoices.",
        session=_SINGLE_LOC,
        tier=EvalTier.PLANNING,
        expected=EvalExpectation(
            intent="QUERY",
            verified=None,
        ),
    ),
    # ── APPROVALS ──────────────────────────────────────────────────────────
    EvalCase(
        id="approval-approve-invoice",
        category=EvalCategory.APPROVALS,
        input="Approve the Acme invoice.",
        session=_SINGLE_LOC,
        expected=EvalExpectation(
            intent="APPROVE",
            entity_type="invoice",
            entity_query="acme",
            tool="approve_invoice",
            verified=True,
            require_mutation_tool=True,
        ),
    ),
    EvalCase(
        id="approval-reject-invoice",
        category=EvalCategory.APPROVALS,
        input="Reject the Acme invoice.",
        session=_SINGLE_LOC,
        tier=EvalTier.PLANNING,
        expected=EvalExpectation(
            intent="REJECT",
            entity_type="invoice",
            tool="reject_invoice",
        ),
    ),
    # ── REMINDERS ──────────────────────────────────────────────────────────
    EvalCase(
        id="reminder-call-supplier",
        category=EvalCategory.REMINDERS,
        input="Remind me to call the supplier tomorrow.",
        session=_SINGLE_LOC,
        tier=EvalTier.PLANNING,
        expected=EvalExpectation(
            intent="REMIND",
            entity_type="reminder",
            tool="create_reminder",
            require_mutation_tool=False,
        ),
    ),
    EvalCase(
        id="reminder-payroll-friday",
        category=EvalCategory.REMINDERS,
        input="Set a reminder for payroll on Friday.",
        session=_SINGLE_LOC,
        expected=EvalExpectation(
            intent="REMIND",
            entity_type="reminder",
            tool="create_reminder",
        ),
    ),
    # ── MEETINGS ───────────────────────────────────────────────────────────
    EvalCase(
        id="meeting-schedule-ahmed",
        category=EvalCategory.MEETINGS,
        input="Schedule a meeting with Ahmed tomorrow at 10.",
        session=_SINGLE_LOC,
        tier=EvalTier.PLANNING,
        expected=EvalExpectation(
            intent="SCHEDULE",
            entity_type="meeting",
            tool="create_meeting",
            require_mutation_tool=False,
        ),
    ),
    EvalCase(
        id="meeting-team-standup",
        category=EvalCategory.MEETINGS,
        input="Schedule a team standup for Monday morning.",
        session=_SINGLE_LOC,
        tier=EvalTier.PLANNING,
        expected=EvalExpectation(
            intent="SCHEDULE",
            entity_type="meeting",
            tool="create_meeting",
        ),
    ),
    # ── MULTI-ESTABLISHMENT ────────────────────────────────────────────────
    EvalCase(
        id="multi-incidents-no-context",
        category=EvalCategory.MULTI_ESTABLISHMENT,
        input="What are today's incidents?",
        session={**_MULTI_LOC, "for_action": "today's incidents"},
        tier=EvalTier.ESTABLISHMENT,
        critical=True,
        expected=EvalExpectation(
            clarify=True,
            response_must_contain=["establishment"],
            verified=False,
            require_mutation_tool=False,
        ),
    ),
    EvalCase(
        id="multi-task-complete-no-context",
        category=EvalCategory.MULTI_ESTABLISHMENT,
        input="Close the decoration task.",
        world=[DECORATION_TASK],
        session=_MULTI_LOC,
        critical=True,
        expected=EvalExpectation(
            intent="COMPLETE",
            clarify=True,
            response_must_contain=["establishment"],
            verified=False,
        ),
    ),
    EvalCase(
        id="multi-switch-casablanca",
        category=EvalCategory.MULTI_ESTABLISHMENT,
        input="What about Casablanca?",
        session={**_MULTI_LOC, "location_id": "loc-rabat", "location_name": "Rabat"},
        tier=EvalTier.ESTABLISHMENT,
        expected=EvalExpectation(
            context={"location_id": "loc-rabat"},
        ),
    ),
    # ── WHATSAPP ───────────────────────────────────────────────────────────
    EvalCase(
        id="whatsapp-complete-decoration",
        category=EvalCategory.WHATSAPP,
        input="Close the decoration task.",
        channel="whatsapp",
        world=[DECORATION_TASK],
        session=_SINGLE_LOC,
        expected=EvalExpectation(
            intent="COMPLETE",
            entity_type="task",
            tool="complete_task",
            context={"channel": "whatsapp"},
            db_state={"status": "COMPLETED"},
            verified=True,
            require_mutation_tool=True,
        ),
    ),
    EvalCase(
        id="whatsapp-report-incident",
        category=EvalCategory.WHATSAPP,
        input="Report a broken glass incident in the dining room.",
        channel="whatsapp",
        session=_SINGLE_LOC,
        expected=EvalExpectation(
            intent="CREATE",
            entity_type="incident",
            context={"channel": "whatsapp"},
            tool="create_incident",
        ),
    ),
    # ── VOICE ──────────────────────────────────────────────────────────────
    EvalCase(
        id="voice-complete-decoration",
        category=EvalCategory.VOICE,
        input="Close the decoration task.",
        channel="voice",
        world=[DECORATION_TASK],
        session=_SINGLE_LOC,
        expected=EvalExpectation(
            intent="COMPLETE",
            entity_type="task",
            tool="complete_task",
            context={"channel": "voice"},
            db_state={"status": "COMPLETED"},
            verified=True,
            require_mutation_tool=True,
        ),
    ),
    EvalCase(
        id="voice-staff-lookup",
        category=EvalCategory.VOICE,
        input="Who is Ahmed?",
        channel="voice",
        session=_SINGLE_LOC,
        tier=EvalTier.PLANNING,
        expected=EvalExpectation(
            intent="QUERY",
            entity_type="staff",
            tool="find_staff",
            context={"channel": "voice"},
            verified=None,
        ),
    ),
    # ── AMBIGUOUS REQUESTS ─────────────────────────────────────────────────
    EvalCase(
        id="ambiguous-multiple-checklists",
        category=EvalCategory.AMBIGUOUS,
        input="Close the checklist task.",
        world=[CHECKLIST_A, CHECKLIST_B],
        session=_SINGLE_LOC,
        critical=True,
        expected=EvalExpectation(
            intent="COMPLETE",
            entity_type="task",
            clarify=True,
            response_must_contain=["which"],
            verified=False,
            require_mutation_tool=False,
        ),
    ),
    EvalCase(
        id="ambiguous-complete-it-no-context",
        category=EvalCategory.AMBIGUOUS,
        input="Complete it.",
        session=_SINGLE_LOC,
        critical=True,
        expected=EvalExpectation(
            intent="COMPLETE",
            clarify=True,
            response_must_contain=["task"],
            verified=False,
        ),
    ),
    EvalCase(
        id="ambiguous-other-branch",
        category=EvalCategory.AMBIGUOUS,
        input="Do the same for the other branch.",
        session=_SINGLE_LOC,
        tier=EvalTier.PLANNING,
        expected=EvalExpectation(
            intent="UNKNOWN",
            verified=None,
        ),
        notes="Cross-establishment ambiguity must defer — never guess.",
    ),
    # ── PERMISSION TESTS ───────────────────────────────────────────────────
    EvalCase(
        id="permission-staff-cannot-approve",
        category=EvalCategory.PERMISSIONS,
        input="Approve the Acme invoice.",
        role="STAFF",
        session=_SINGLE_LOC,
        critical=True,
        expected=EvalExpectation(
            intent="APPROVE",
            entity_type="invoice",
            permission_allowed=False,
            verified=False,
            response_must_contain=["permission"],
        ),
    ),
    EvalCase(
        id="permission-staff-cannot-create-task",
        category=EvalCategory.PERMISSIONS,
        input="Create a task for Ahmed to clean the patio.",
        role="STAFF",
        session=_SINGLE_LOC,
        tier=EvalTier.PLANNING,
        expected=EvalExpectation(
            intent="CREATE",
            entity_type="task",
            verified=None,
        ),
        notes="Execution permission enforced in ops layer; intent must be recognized.",
    ),
]
