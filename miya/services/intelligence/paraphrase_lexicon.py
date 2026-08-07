"""
Structured paraphrase lexicon — deterministic hospitality/restaurant language.

Not an uncontrolled regex pile: each rule is an explicit (pattern → intent, entity, slots)
entry applied in priority order after base classification.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from miya.services.intelligence.planning.types import (
    ClassifiedIntent,
    Confidence,
    EntityType,
    IntentClass,
)

# ── Rule table (priority high → low) ─────────────────────────────────────────

@dataclass(frozen=True)
class ParaphraseRule:
    id: str
    pattern: re.Pattern[str]
    intent: IntentClass
    entity: EntityType
    status_hint: str = ""
    assignee_group: int | None = None  # regex group for assignee name
    query_group: int | None = None
    confidence: Confidence = Confidence.HIGH
    reason: str = ""


def _r(pat: str, flags: int = re.I) -> re.Pattern[str]:
    return re.compile(pat, flags)


RULES: tuple[ParaphraseRule, ...] = (
    # ── TASK complete ──
    ParaphraseRule("task-done-that", _r(r"^\s*(?:that|this)\s+(?:task\s+)?(?:is\s+)?done\s*[.!]?\s*$"), IntentClass.COMPLETE, EntityType.TASK, "COMPLETED", reason="task_done_pronoun"),
    ParaphraseRule("task-finished-closing", _r(r"\b(?:ahmed|staff)\s+finished\s+(?:the\s+)?(.+?)\s*[.!]?\s*$"), IntentClass.COMPLETE, EntityType.TASK, "COMPLETED", query_group=1, reason="staff_finished_task"),
    ParaphraseRule("mark-complete-named", _r(r"\bmark\s+(?:the\s+)?(.+?)\s+(?:task\s+)?complete\b"), IntentClass.COMPLETE, EntityType.TASK, "COMPLETED", query_group=1, reason="mark_named_complete"),
    ParaphraseRule("close-named-task", _r(r"\bclose\s+(?:the\s+)?(.+?)\s+task\b"), IntentClass.COMPLETE, EntityType.TASK, "COMPLETED", query_group=1, reason="close_named_task"),
    # ── TASK assign ──
    ParaphraseRule("give-task-to", _r(r"\bgive\s+(?:the\s+)?(.+?)\s+(?:task\s+)?to\s+([A-Za-zÀ-ÿ][\w\-']+)"), IntentClass.ASSIGN, EntityType.TASK, assignee_group=2, query_group=1, reason="give_task_to"),
    ParaphraseRule("put-on-task", _r(r"\bput\s+([A-Za-zÀ-ÿ][\w\-']+)\s+on\s+(.+?)\s*[.!]?\s*$"), IntentClass.ASSIGN, EntityType.TASK, assignee_group=1, query_group=2, reason="put_staff_on"),
    ParaphraseRule("should-handle", _r(r"\b([A-Za-zÀ-ÿ][\w\-']+)\s+should\s+handle\s+(.+?)\s*[.!]?\s*$"), IntentClass.ASSIGN, EntityType.TASK, assignee_group=1, query_group=2, reason="should_handle"),
    ParaphraseRule("move-task-to", _r(r"\bmove\s+(?:this|the|that)\s+task\s+to\s+([A-Za-zÀ-ÿ][\w\-']+)"), IntentClass.ASSIGN, EntityType.TASK, assignee_group=1, reason="move_task_to"),
    # ── INCIDENT create ──
    ParaphraseRule("freezer-broken", _r(r"\b(?:the\s+)?freezer\s+(?:is\s+)?(?:broken|stopped|not working|down)\b"), IntentClass.CREATE, EntityType.INCIDENT, reason="freezer_broken"),
    ParaphraseRule("issue-with-freezer", _r(r"\b(?:there(?:'s| is)|we have)\s+(?:an?\s+)?issue\s+with\s+(?:the\s+)?freezer\b"), IntentClass.CREATE, EntityType.INCIDENT, reason="issue_freezer"),
    ParaphraseRule("report-this", _r(r"^\s*(?:report|log)\s+(?:this|that)\s*[.!]?\s*$"), IntentClass.CREATE, EntityType.INCIDENT, reason="report_this"),
    ParaphraseRule("send-to-maintenance", _r(r"\bsend\s+(?:this|it|that)\s+to\s+maintenance\b"), IntentClass.ROUTE, EntityType.INCIDENT, reason="send_maintenance"),
    ParaphraseRule("forward-to-hr", _r(r"\bforward\s+(?:this|it|that)\s+to\s+hr\b"), IntentClass.ROUTE, EntityType.CATEGORY, reason="forward_hr"),
    # ── INCIDENT close (not task) ──
    ParaphraseRule("close-incident", _r(r"\bclose\s+(?:the\s+)?(.+?)\s+incident\b"), IntentClass.COMPLETE, EntityType.INCIDENT, "RESOLVED", query_group=1, reason="close_incident"),
    # ── STATUS / history queries ──
    ParaphraseRule("status-of", _r(r"\bwhat\s+is\s+the\s+status\s+of\s+(.+?)\s*[?.!]?\s*$"), IntentClass.QUERY, EntityType.TASK, query_group=1, confidence=Confidence.HIGH, reason="status_of"),
    ParaphraseRule("who-handling", _r(r"\bwho\s+is\s+handling\s+(.+?)\s*[?.!]?\s*$"), IntentClass.QUERY, EntityType.TASK, query_group=1, reason="who_handling"),
    ParaphraseRule("why-pending", _r(r"\bwhy\s+is\s+(?:it|this|that|(.+?))\s+still\s+pending\b"), IntentClass.QUERY, EntityType.TASK, query_group=1, reason="why_pending"),
    ParaphraseRule("what-happened-to", _r(r"\bwhat\s+happened\s+(?:to|with)\s+(.+?)\s*[?.!]?\s*$"), IntentClass.QUERY, EntityType.UNKNOWN, query_group=1, reason="what_happened"),
    ParaphraseRule("who-changed", _r(r"\bwho\s+changed\s+(?:the\s+)?(.+?)\s*[?.!]?\s*$"), IntentClass.QUERY, EntityType.TASK, query_group=1, reason="who_changed"),
    ParaphraseRule("when-completed", _r(r"\bwhen\s+was\s+(.+?)\s+(?:completed|finished|closed)\b"), IntentClass.QUERY, EntityType.TASK, query_group=1, reason="when_completed"),
    # ── DOCUMENT ──
    ParaphraseRule("show-insurance", _r(r"\bshow\s+(?:me\s+)?(?:the\s+)?insurance\b"), IntentClass.RETRIEVE, EntityType.DOCUMENT, query_group=None, reason="show_insurance"),
    ParaphraseRule("insurance-expire", _r(r"\bwhen\s+does\s+(?:the\s+)?insurance\s+expire\b"), IntentClass.QUERY, EntityType.DOCUMENT, reason="insurance_expiry"),
    ParaphraseRule("remind-insurance", _r(r"\bremind\s+(?:me\s+)?(?:about\s+)?(?:the\s+)?insurance\b"), IntentClass.REMIND, EntityType.REMINDER, reason="remind_insurance"),
    ParaphraseRule("pdf-says", _r(r"\bwhat\s+does\s+(?:this|the)\s+pdf\s+say\b"), IntentClass.RETRIEVE, EntityType.DOCUMENT, reason="pdf_says"),
    # ── INVOICE ──
    ParaphraseRule("approve-invoice", _r(r"\bapprove\s+(?:this|the)\s+invoice\b"), IntentClass.APPROVE, EntityType.INVOICE, reason="approve_invoice"),
    ParaphraseRule("why-not-paid", _r(r"\bwhy\s+(?:hasn't|has not|isn't)\s+(?:this|the)\s+invoice\s+been\s+paid\b"), IntentClass.QUERY, EntityType.INVOICE, reason="why_not_paid"),
    ParaphraseRule("who-approved-invoice", _r(r"\bwho\s+approved\s+(?:this|the)\s+invoice\b"), IntentClass.QUERY, EntityType.INVOICE, reason="who_approved_invoice"),
    ParaphraseRule("invoice-history", _r(r"\b(?:show|get)\s+(?:me\s+)?(?:the\s+)?invoice\s+history\b"), IntentClass.QUERY, EntityType.INVOICE, reason="invoice_history"),
    # ── MEETINGS ──
    ParaphraseRule("meeting-kitchen", _r(r"\b(?:set up|schedule|arrange)\s+(?:a\s+)?meeting\s+with\s+(?:the\s+)?kitchen\b"), IntentClass.SCHEDULE, EntityType.MEETING, reason="meeting_kitchen"),
    ParaphraseRule("meeting-foh", _r(r"\b(?:set up|schedule|arrange)\s+(?:one|a meeting)\s+for\s+front\s+of\s+house\b"), IntentClass.SCHEDULE, EntityType.MEETING, reason="meeting_foh"),
    ParaphraseRule("meeting-hr", _r(r"\bschedule\s+a\s+meeting\s+with\s+hr\b"), IntentClass.SCHEDULE, EntityType.MEETING, reason="meeting_hr"),
)


def apply_paraphrase_lexicon(classified: ClassifiedIntent) -> ClassifiedIntent:
    """Boost or override classification using structured paraphrase rules."""
    text = (classified.raw_message or "").strip()
    if not text:
        return classified

    for rule in RULES:
        m = rule.pattern.search(text)
        if not m:
            continue
        # Don't downgrade a high-confidence base classification unless rule is more specific
        if classified.confidence == Confidence.HIGH and classified.intent not in (
            IntentClass.UNKNOWN,
            IntentClass.QUERY,
        ):
            if rule.intent == IntentClass.QUERY and classified.intent != IntentClass.UNKNOWN:
                continue

        classified.intent = rule.intent
        if rule.entity != EntityType.UNKNOWN:
            classified.entity_type = rule.entity
        if rule.status_hint:
            classified.status_hint = rule.status_hint
        if rule.confidence:
            classified.confidence = rule.confidence
        if rule.assignee_group and m.lastindex and m.lastindex >= rule.assignee_group:
            classified.assignee_hint = m.group(rule.assignee_group).strip()
        if rule.query_group and m.lastindex and m.lastindex >= rule.query_group:
            q = m.group(rule.query_group).strip(" .!?,")
            if q:
                classified.query = q
        elif rule.id in ("show-insurance", "remind-insurance"):
            classified.query = "insurance"
        classified.reasons.append(f"paraphrase:{rule.id}")
        break

    return classified


def normalize_channel(channel: str) -> str:
    """Canonical channel id for routing (dashboard/whatsapp/mobile/voice/miya)."""
    c = (channel or "dashboard").strip().lower()
    aliases = {
        "web": "dashboard",
        "app": "dashboard",
        "wa": "whatsapp",
        "phone": "voice",
        "speech": "voice",
        "miya_agent": "miya",
    }
    return aliases.get(c, c)
