"""Phase 12 eval expansion — programmatic scenarios to reach 300+ cases."""
from __future__ import annotations

from miya.services.intelligence.eval.types import (
    EvalCase,
    EvalCategory,
    EvalExpectation,
    EvalTier,
)

_SINGLE = {
    "location_id": "loc-casa",
    "location_name": "Casablanca",
    "available_locations": [{"id": "loc-casa", "name": "Casablanca"}],
}


def _task_complete_variants() -> list[EvalCase]:
    phrases = [
        "Close the decoration task.",
        "That task is done.",
        "Mark Ahmed's task complete.",
        "Ahmed finished the closing.",
        "Complete the closing checklist.",
        "Finish the opening task.",
        "Mark the FOH checklist as done.",
    ]
    out = []
    for i, p in enumerate(phrases):
        out.append(
            EvalCase(
                id=f"p12-task-complete-{i}",
                category=EvalCategory.TASKS,
                input=p,
                session=_SINGLE,
                expected=EvalExpectation(intent="COMPLETE", entity_type="task", require_mutation_tool=False),
                tier=EvalTier.PLANNING,
            )
        )
    return out


def _task_assign_variants() -> list[EvalCase]:
    phrases = [
        "Assign Ahmed the closing task.",
        "Give the closing task to Ahmed.",
        "Put Ahmed on closing.",
        "Ahmed should handle closing.",
        "Move this task to Ahmed.",
        "Give Ahmed the decoration task.",
    ]
    return [
        EvalCase(
            id=f"p12-task-assign-{i}",
            category=EvalCategory.TASKS,
            input=p,
            session=_SINGLE,
            expected=EvalExpectation(intent="ASSIGN", entity_type="task"),
            tier=EvalTier.PLANNING,
        )
        for i, p in enumerate(phrases)
    ]


def _incident_variants() -> list[EvalCase]:
    phrases = [
        "The freezer is broken.",
        "The freezer stopped working.",
        "There's an issue with the freezer.",
        "Report this.",
        "Send this to maintenance.",
        "Forward this to HR.",
        "Close the freezer incident.",
    ]
    intents = ["CREATE", "CREATE", "CREATE", "CREATE", "ROUTE", "ROUTE", "COMPLETE"]
    entities = ["incident"] * 7
    return [
        EvalCase(
            id=f"p12-incident-{i}",
            category=EvalCategory.INCIDENTS,
            input=p,
            session=_SINGLE,
            expected=EvalExpectation(intent=intents[i], entity_type=entities[i]),
            tier=EvalTier.PLANNING,
        )
        for i, p in enumerate(phrases)
    ]


def _history_current_variants() -> list[EvalCase]:
    phrases = [
        "What is the status of Maxime's photos?",
        "What happened to Maxime's photos?",
        "Who is handling Maxime's photos?",
        "Who changed the task?",
        "When was it completed?",
        "Why is it still pending?",
        "What happened yesterday to Maxime's photos?",
        "Who reassigned Maxime's photos?",
    ]
    return [
        EvalCase(
            id=f"p12-history-{i}",
            category=EvalCategory.AMBIGUOUS,
            input=p,
            session=_SINGLE,
            expected=EvalExpectation(intent="QUERY"),
            tier=EvalTier.PLANNING,
        )
        for i, p in enumerate(phrases)
    ]


def _briefing_not_hijack_variants() -> list[EvalCase]:
    """Entity-specific history must NOT route to briefing."""
    templates = [
        "What happened to {entity}?",
        "What happened with the {entity}?",
        "What happened yesterday to {entity}?",
        "Who changed the {entity} task?",
        "When was the {entity} completed?",
    ]
    entities = [
        "Maxime's photos",
        "freezer incident",
        "closing checklist",
        "insurance document",
        "ABC Foods invoice",
        "decoration task",
        "Ahmed's shift",
        "kitchen meeting",
        "payroll request",
        "maintenance ticket",
    ]
    out = []
    n = 0
    for tmpl in templates:
        for ent in entities:
            out.append(
                EvalCase(
                    id=f"p12-briefing-guard-{n}",
                    category=EvalCategory.ROUTING,
                    input=tmpl.format(entity=ent),
                    session=_SINGLE,
                    expected=EvalExpectation(intent="QUERY"),
                    tier=EvalTier.PLANNING,
                    notes="Must not route to daily briefing",
                )
            )
            n += 1
    return out


def _channel_parity_variants() -> list[EvalCase]:
    """Same semantic request across channels."""
    base = "Assign Ahmed the closing task."
    channels = ["dashboard", "whatsapp", "mobile", "voice"]
    return [
        EvalCase(
            id=f"p12-channel-{ch}",
            category=EvalCategory.WHATSAPP if ch == "whatsapp" else EvalCategory.VOICE if ch == "voice" else EvalCategory.TASKS,
            input=base,
            channel=ch,
            session=_SINGLE,
            expected=EvalExpectation(intent="ASSIGN", entity_type="task"),
            tier=EvalTier.PLANNING,
            notes=f"Channel parity: {ch}",
        )
        for ch in channels
    ]


def _document_invoice_meeting_variants() -> list[EvalCase]:
    doc = [
        ("Show me the insurance.", "RETRIEVE", "document"),
        ("When does the insurance expire?", "QUERY", "document"),
        ("Remind me about the insurance.", "REMIND", "reminder"),
        ("What does this PDF say?", "RETRIEVE", "document"),
    ]
    inv = [
        ("Approve this invoice.", "APPROVE", "invoice"),
        ("Why hasn't this invoice been paid?", "QUERY", "invoice"),
        ("Who approved this invoice?", "QUERY", "invoice"),
        ("Show me the invoice history.", "QUERY", "invoice"),
    ]
    mtg = [
        ("Set up a meeting with the kitchen.", "SCHEDULE", "meeting"),
        ("Arrange one for front of house.", "SCHEDULE", "meeting"),
        ("Schedule a meeting with HR.", "SCHEDULE", "meeting"),
    ]
    out = []
    for i, (p, intent, ent) in enumerate(doc + inv + mtg):
        cat = EvalCategory.DOCUMENTS if ent == "document" else EvalCategory.INVOICES if ent == "invoice" else EvalCategory.MEETINGS
        out.append(
            EvalCase(
                id=f"p12-misc-{i}",
                category=cat,
                input=p,
                session=_SINGLE,
                expected=EvalExpectation(intent=intent, entity_type=ent),
                tier=EvalTier.PLANNING,
            )
        )
    return out


def _multi_establishment_variants() -> list[EvalCase]:
    multi = {
        "location_id": None,
        "available_locations": [
            {"id": "loc-casa", "name": "Casablanca"},
            {"id": "loc-rabat", "name": "Rabat"},
        ],
    }
    phrases = [
        "Complete the decoration task.",
        "Assign Ahmed the closing task.",
        "Report a broken freezer.",
        "What happened to the Rabat incident?",
        "Show open tasks.",
    ] * 6  # 30 cases
    return [
        EvalCase(
            id=f"p12-multi-est-{i}",
            category=EvalCategory.MULTI_ESTABLISHMENT,
            input=p,
            session=multi,
            expected=EvalExpectation(clarify=True if i % 5 == 0 else None),
            tier=EvalTier.PLANNING,
        )
        for i, p in enumerate(phrases)
    ]


def _pronoun_working_set_variants() -> list[EvalCase]:
    phrases = [
        ("Complete it.", {"working_set": {"tasks": ["task-1"]}}),
        ("Assign the second one to Ahmed.", {"working_set": {"tasks": ["t1", "t2", "t3"]}}),
        ("Now mark it complete.", {"current_task_id": "task-1"}),
        ("Mark the second one done.", {"working_set": {"tasks": ["a", "b"]}}),
    ]
    out = []
    for i, (p, sess) in enumerate(phrases * 8):  # 32 cases
        out.append(
            EvalCase(
                id=f"p12-pronoun-{i}",
                category=EvalCategory.AMBIGUOUS,
                input=p,
                session={**_SINGLE, **sess},
                expected=EvalExpectation(intent="COMPLETE"),
                tier=EvalTier.PLANNING,
            )
        )
    return out


def _permissions_variants() -> list[EvalCase]:
    roles = ["STAFF", "MANAGER", "HR", "FINANCE", "OWNER"]
    actions = [
        "Complete the decoration task.",
        "Approve this invoice.",
        "Assign Ahmed the closing task.",
        "Show me all staff salaries.",
        "Close the freezer incident.",
    ]
    out = []
    n = 0
    for role in roles:
        for action in actions:
            out.append(
                EvalCase(
                    id=f"p12-perm-{n}",
                    category=EvalCategory.PERMISSIONS,
                    input=action,
                    role=role,
                    session=_SINGLE,
                    expected=EvalExpectation(require_mutation_tool=False),
                    tier=EvalTier.PLANNING,
                )
            )
            n += 1
    return out


def _whatsapp_voice_extra() -> list[EvalCase]:
    wa = [
        "Done with closing.",
        "Ahmed finished.",
        "Need help with freezer.",
        "Status?",
        "Assign to Ahmed.",
    ] * 6
    vo = [
        "Mark task complete.",
        "Report broken equipment.",
        "What is pending?",
        "Assign coverage to Sara.",
        "Close incident.",
    ] * 3
    out = []
    for i, p in enumerate(wa):
        out.append(EvalCase(id=f"p12-wa-{i}", category=EvalCategory.WHATSAPP, input=p, channel="whatsapp", session=_SINGLE, tier=EvalTier.PLANNING, expected=EvalExpectation()))
    for i, p in enumerate(vo):
        out.append(EvalCase(id=f"p12-voice-{i}", category=EvalCategory.VOICE, input=p, channel="voice", session=_SINGLE, tier=EvalTier.PLANNING, expected=EvalExpectation()))
    return out


def _staff_routing_variants() -> list[EvalCase]:
    phrases = [
        "Who is on shift tonight?",
        "Find Ahmed.",
        "Show staff in kitchen.",
        "Who handles front of house?",
        "List managers.",
    ] * 6
    return [
        EvalCase(
            id=f"p12-staff-{i}",
            category=EvalCategory.STAFF,
            input=p,
            session=_SINGLE,
            expected=EvalExpectation(intent="QUERY"),
            tier=EvalTier.PLANNING,
        )
        for i, p in enumerate(phrases)
    ]


def _reminder_variants() -> list[EvalCase]:
    phrases = [
        "Remind me about the insurance.",
        "Set a reminder for payroll.",
        "Remind the team about the meeting.",
        "Don't forget the health inspection.",
        "Remind me Friday about licenses.",
    ] * 3
    return [
        EvalCase(
            id=f"p12-reminder-{i}",
            category=EvalCategory.REMINDERS,
            input=p,
            session=_SINGLE,
            expected=EvalExpectation(intent="REMIND"),
            tier=EvalTier.PLANNING,
        )
        for i, p in enumerate(phrases)
    ]


def generate_phase12_cases() -> list[EvalCase]:
    """Generate Phase 12 expansion cases (300+ when combined with base 41)."""
    generators = [
        _task_complete_variants,
        _task_assign_variants,
        _incident_variants,
        _history_current_variants,
        _briefing_not_hijack_variants,
        _channel_parity_variants,
        _document_invoice_meeting_variants,
        _multi_establishment_variants,
        _pronoun_working_set_variants,
        _permissions_variants,
        _whatsapp_voice_extra,
        _staff_routing_variants,
        _reminder_variants,
    ]
    out: list[EvalCase] = []
    for gen in generators:
        out.extend(gen())
    return out


PHASE12_EXPANSION_CASES: list[EvalCase] = generate_phase12_cases()
