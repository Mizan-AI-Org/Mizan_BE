"""UNDERSTAND + IDENTIFY — classify intent and entity from user text."""
from __future__ import annotations

import re
from typing import Any

from miya.services.intelligence.planning.types import (
    ClassifiedIntent,
    Confidence,
    EntityType,
    IntentClass,
)

_PRONOUN = re.compile(
    r"\b(it|that|this|them|those|these|ça|ca|le|la|les|celui[- ]?là|celle[- ]?là)\b",
    re.I,
)

_COMPLETE = re.compile(
    r"\b(close|closed|complete|completed|finish|finished|done|mark\s+as\s+done|"
    r"terminer|terminé|clôtur|clotur)\b",
    re.I,
)
_ASSIGN = re.compile(
    r"\b(assign|reassign|give|delegate|passe[rz]?|assigne[rz]?|send\s+(?:this|it|that)\s+to)\b",
    re.I,
)
_CREATE_INCIDENT = re.compile(
    r"\b(create|log|report|open)\b.+\b(incident|accident|safety)\b|"
    r"\b(incident|accident)\b.+\b(create|log|report)\b",
    re.I,
)
_REPORT_THIS = re.compile(
    r"^\s*(report|log|signal)\s+(this|that|it|ça|ca)\s*[.!]?\s*$|"
    r"^\s*(report\s+this|log\s+this)\s*[.!]?\s*$",
    re.I,
)
_STAFF_LOOKUP = re.compile(
    r"\b(who\s+is|find\s+staff|look\s*up\s+staff|staff\s+(?:named|called)|"
    r"trouver|cherche)\b|\b(where\s+is)\s+[A-ZÀ-Ÿ]",
    re.I,
)
_CREATE = re.compile(r"\b(create|add|new|make|ouvrir|créer|creer)\b", re.I)
_APPROVE = re.compile(r"\b(approve|approuv|accept\s+payment|sign\s+off)\b", re.I)
_REJECT = re.compile(r"\b(reject|deny|refuse|refus)\b", re.I)
_ROUTE = re.compile(r"\b(route|escalat|forward\s+to|send\s+to\s+(?:hr|maintenance|kitchen))\b", re.I)
_REMIND = re.compile(r"\b(remind|reminder|rappelle|rappel)\b", re.I)
_SCHEDULE = re.compile(r"\b(schedule|meeting|calendar|rendez[- ]?vous|réunion|reunion)\b", re.I)
_RETRIEVE = re.compile(
    r"\b(show|get|open|find|retrieve|fetch|display|affiche|montre)\b.+\b"
    r"(document|file|invoice|insurance|pdf|photo)\b|"
    r"\b(document|insurance|invoice)\b",
    re.I,
)
_UPLOAD = re.compile(r"\b(upload|attach|joindre|télévers)\b", re.I)
_DELETE = re.compile(r"\b(delete|remove|cancel|annule|supprime)\b", re.I)
_ANALYZE = re.compile(r"\b(analyze|analyse|inspect|diagnose)\b", re.I)
_SUMMARIZE = re.compile(r"\b(summarize|summary|brief|recap|résum|resum)\b", re.I)
_QUERY = re.compile(
    r"\b(who|what|which|where|when|how|status|list|show|pending|is\s+.+\s+(done|completed))\b",
    re.I,
)

_TASK_ENTITY = re.compile(r"\b(task|checklist|demande|tâche|tache)\b", re.I)
_INCIDENT_ENTITY = re.compile(r"\b(incident|accident|freezer|frigo|safety)\b", re.I)
_DOC_ENTITY = re.compile(r"\b(document|insurance|file|pdf|contrat)\b", re.I)
_INVOICE_ENTITY = re.compile(r"\b(invoice|facture|bill|payguard)\b", re.I)
_STAFF_ENTITY = re.compile(r"\b(staff|employee|waiter|chef|ahmed|sara)\b", re.I)
_CATEGORY_ENTITY = re.compile(r"\b(hr|payroll|maintenance|deliveries|finance|category)\b", re.I)
_EST_ENTITY = re.compile(r"\b(branch|establishment|location|casablanca|rabat|other\s+branch)\b", re.I)
_MEETING_ENTITY = re.compile(r"\b(meeting|calendar|réunion|reunion)\b", re.I)
_REMINDER_ENTITY = re.compile(r"\b(remind|reminder|rappel)\b", re.I)

_ASSIGN_TO = re.compile(
    r"(?:to|à|a|au)\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\-']+(?:\s+[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\-']+)?)",
    re.I,
)
_TASK_TITLE = re.compile(
    r"(?:close|complete|finish|mark|update|assign|reassign)\s+(?:the\s+)?(.+?)\s+task\b|"
    r"\btask\s+(?:called\s+|named\s+)?['\"]?(.+?)['\"]?\s*$|"
    r"(?:close|complete|finish)\s+(?:the\s+)?(.+?)(?:\s*[.!]|$)",
    re.I,
)
_UNDER_ESTABLISHMENT = re.compile(
    r"\b(?:under|at|for|in)\s+([A-Za-z0-9][A-Za-z0-9\s\-']+?)(?:\s*,|\s+it(?:'s|s)\s|\s*$)",
    re.I,
)


def classify_message(
    message: str,
    *,
    session_context: dict[str, Any] | None = None,
    multimodal: dict[str, Any] | None = None,
) -> ClassifiedIntent:
    text = (message or "").strip()
    # Strip attachment enrichment prefix for classification when present
    classify_text = text
    if "User message:" in text:
        classify_text = text.split("User message:", 1)[-1].strip()
    reasons: list[str] = []
    mm = multimodal or (session_context or {}).get("_multimodal")
    if not isinstance(mm, dict):
        mm = None

    if not classify_text and not (mm and mm.get("attachments")):
        return ClassifiedIntent(
            intent=IntentClass.UNKNOWN,
            entity_type=EntityType.UNKNOWN,
            confidence=Confidence.LOW,
            raw_message=text,
            reasons=["empty_message"],
        )

    intent = IntentClass.UNKNOWN
    entity = EntityType.UNKNOWN
    pronoun = bool(_PRONOUN.search(classify_text))
    query = ""
    assignee = ""
    status_hint = ""

    # Intent (order matters — more specific first)
    if _REPORT_THIS.search(classify_text):
        intent, entity = IntentClass.CREATE, EntityType.INCIDENT
        reasons.append("report_this_phrase")
    elif (
        mm
        and mm.get("suggested_intent") == "CREATE"
        and mm.get("suggested_entity") == "incident"
        and not classify_text
    ):
        intent, entity = IntentClass.CREATE, EntityType.INCIDENT
        reasons.append("media_only_incident")
    elif _CREATE_INCIDENT.search(classify_text):
        intent, entity = IntentClass.CREATE, EntityType.INCIDENT
        reasons.append("create_incident_phrase")
    elif _COMPLETE.search(classify_text) and _INCIDENT_ENTITY.search(classify_text):
        intent = IntentClass.COMPLETE
        entity = EntityType.INCIDENT
        status_hint = "RESOLVED"
        reasons.append("close_incident_phrase")
    elif _COMPLETE.search(classify_text) and (
        _TASK_ENTITY.search(classify_text)
        or pronoun
        or re.search(r"\b(close|complete|finish)\b.+\b\w+", classify_text, re.I)
    ):
        intent = IntentClass.COMPLETE
        entity = EntityType.TASK
        status_hint = "COMPLETED"
        reasons.append("complete_phrase")
    if intent == IntentClass.UNKNOWN and _ASSIGN.search(classify_text):
        intent = IntentClass.ASSIGN
        entity = (
            EntityType.TASK
            if not _INCIDENT_ENTITY.search(classify_text)
            else EntityType.INCIDENT
        )
        if re.search(r"\bhr\b", classify_text, re.I):
            entity = EntityType.CATEGORY if "send" in classify_text.lower() else entity
        reasons.append("assign_phrase")
        m = _ASSIGN_TO.search(classify_text)
        if m:
            assignee = m.group(1).strip()
    if intent == IntentClass.UNKNOWN and _APPROVE.search(classify_text):
        intent, entity = IntentClass.APPROVE, EntityType.INVOICE
        reasons.append("approve_phrase")
    if intent == IntentClass.UNKNOWN and _REJECT.search(classify_text):
        intent, entity = IntentClass.REJECT, EntityType.INVOICE
        reasons.append("reject_phrase")
    if intent == IntentClass.UNKNOWN and _ROUTE.search(classify_text):
        intent = IntentClass.ROUTE
        entity = (
            EntityType.INCIDENT
            if _INCIDENT_ENTITY.search(classify_text)
            else EntityType.CATEGORY
        )
        reasons.append("route_phrase")
    if intent == IntentClass.UNKNOWN and _REMIND.search(classify_text):
        intent, entity = IntentClass.REMIND, EntityType.REMINDER
        reasons.append("remind_phrase")
    if intent == IntentClass.UNKNOWN and _SCHEDULE.search(classify_text) and not _REMIND.search(
        classify_text
    ):
        intent, entity = IntentClass.SCHEDULE, EntityType.MEETING
        reasons.append("schedule_phrase")
    if intent == IntentClass.UNKNOWN and _UPLOAD.search(classify_text):
        intent, entity = IntentClass.UPLOAD, EntityType.DOCUMENT
        reasons.append("upload_phrase")
    if intent == IntentClass.UNKNOWN and _DELETE.search(classify_text):
        intent = IntentClass.DELETE
        entity = _guess_entity(classify_text)
        reasons.append("delete_phrase")
    if intent == IntentClass.UNKNOWN and _ANALYZE.search(classify_text):
        intent, entity = IntentClass.ANALYZE, _guess_entity(classify_text)
        reasons.append("analyze_phrase")
    if intent == IntentClass.UNKNOWN and _SUMMARIZE.search(classify_text):
        intent, entity = IntentClass.SUMMARIZE, EntityType.UNKNOWN
        reasons.append("summarize_phrase")
    if intent == IntentClass.UNKNOWN and _RETRIEVE.search(classify_text):
        intent = IntentClass.RETRIEVE
        entity = (
            EntityType.DOCUMENT
            if _DOC_ENTITY.search(classify_text)
            else _guess_entity(classify_text)
        )
        reasons.append("retrieve_phrase")
    if intent == IntentClass.UNKNOWN and _STAFF_LOOKUP.search(classify_text):
        intent, entity = IntentClass.QUERY, EntityType.STAFF
        reasons.append("staff_lookup_phrase")
    if intent == IntentClass.UNKNOWN and _CREATE.search(classify_text):
        intent = IntentClass.CREATE
        entity = _guess_entity(classify_text)
        if entity == EntityType.UNKNOWN:
            entity = EntityType.TASK
        reasons.append("create_phrase")
    if intent == IntentClass.UNKNOWN and _QUERY.search(classify_text):
        intent = IntentClass.QUERY
        entity = _guess_entity(classify_text)
        reasons.append("query_phrase")

    # Refine entity if still unknown
    if entity == EntityType.UNKNOWN:
        entity = _guess_entity(classify_text)

    # Title / query extraction
    query = _extract_query(classify_text, intent, entity)
    if pronoun:
        reasons.append("pronoun_reference")

    confidence = _base_confidence(intent, entity, query, pronoun, assignee, classify_text)

    # Cross-establishment phrase → force low until clarified
    if re.search(r"\b(other\s+branch|same\s+for|autre\s+succursale)\b", classify_text, re.I):
        confidence = Confidence.LOW
        reasons.append("cross_establishment_ambiguity")

    classified = ClassifiedIntent(
        intent=intent,
        entity_type=entity,
        confidence=confidence,
        query=query,
        assignee_hint=assignee,
        status_hint=status_hint,
        pronoun=pronoun,
        raw_message=classify_text or text,
        slots={
            "session_establishment_id": (session_context or {}).get("location_id"),
        },
        reasons=reasons,
    )
    est_hint = _extract_establishment_hint(classify_text or text)
    if est_hint:
        classified.slots["establishment_hint"] = est_hint
        reasons.append("establishment_hint")
    return _apply_multimodal(classified, mm)


def _extract_establishment_hint(text: str) -> str:
    m = _UNDER_ESTABLISHMENT.search(text or "")
    if not m:
        return ""
    hint = (m.group(1) or "").strip(" .,!?'\"")
    if hint.lower() in ("done", "today", "now", "please"):
        return ""
    return hint


def _apply_multimodal(
    classified: ClassifiedIntent,
    mm: dict[str, Any] | None,
) -> ClassifiedIntent:
    """OCR/vision fields become slots for reasoning — never the final decision alone."""
    if not mm or not mm.get("attachments"):
        return classified

    primary = (mm.get("attachments") or [{}])[0] or {}
    classified.slots["multimodal"] = True
    classified.slots["document_id"] = str(primary.get("document_id") or "")
    classified.slots["media_kind"] = mm.get("primary_kind") or primary.get("kind") or ""
    classified.slots["structured"] = primary.get("structured") or {}
    classified.slots["summary"] = primary.get("summary") or ""
    classified.slots["vendor"] = primary.get("vendor") or ""
    classified.slots["amount"] = primary.get("amount") or ""
    classified.slots["currency"] = primary.get("currency") or ""
    classified.slots["invoice_number"] = primary.get("invoice_number") or ""
    classified.slots["expiry_date"] = primary.get("expiry_date") or ""
    classified.slots["invoice_id"] = primary.get("invoice_id") or ""
    classified.slots["compliance_document_id"] = primary.get("compliance_document_id") or ""
    classified.slots["attachment_title"] = primary.get("title") or ""
    classified.reasons.append("multimodal_attachment")

    sug_i = (mm.get("suggested_intent") or "").upper()
    sug_e = (mm.get("suggested_entity") or "").lower()
    weak = classified.intent in (IntentClass.UNKNOWN, IntentClass.ANALYZE, IntentClass.SUMMARIZE)
    reportish = bool(_REPORT_THIS.search(classified.raw_message or ""))
    media_only = not (classified.raw_message or "").strip()

    # Image → incident retrieval when user asks to find/show with incident photo
    if (
        classified.intent == IntentClass.RETRIEVE
        and _INCIDENT_ENTITY.search(classified.raw_message or "")
    ) or (
        classified.intent in (IntentClass.QUERY, IntentClass.RETRIEVE)
        and (mm.get("primary_kind") or "") in ("incident_photo", "equipment")
        and re.search(r"\b(find|show|get|retrieve|open)\b.+\bincident", classified.raw_message or "", re.I)
    ):
        classified.intent = IntentClass.RETRIEVE
        classified.entity_type = EntityType.INCIDENT
        classified.confidence = Confidence.HIGH
        classified.reasons.append("multimodal_incident_retrieve")
        if not classified.query and classified.slots.get("summary"):
            classified.query = str(classified.slots["summary"])[:120]
        return classified

    if sug_i and (weak or reportish or media_only or classified.intent == IntentClass.CREATE):
        try:
            classified.intent = IntentClass[sug_i]
        except KeyError:
            pass
        entity_map = {
            "incident": EntityType.INCIDENT,
            "invoice": EntityType.INVOICE,
            "document": EntityType.DOCUMENT,
            "reminder": EntityType.REMINDER,
            "task": EntityType.TASK,
            "staff": EntityType.STAFF,
        }
        if sug_e in entity_map:
            # Don't overwrite a strong non-task entity unless media-driven create
            if (
                classified.entity_type in (EntityType.UNKNOWN, EntityType.TASK)
                or reportish
                or media_only
                or weak
            ):
                classified.entity_type = entity_map[sug_e]
        classified.confidence = Confidence.HIGH
        classified.reasons.append("multimodal_intent_override")
        if classified.intent == IntentClass.CREATE and classified.entity_type == EntityType.INCIDENT:
            if not classified.query:
                classified.query = (
                    classified.slots.get("summary")
                    or classified.slots.get("attachment_title")
                    or "photo incident"
                )
        if classified.intent == IntentClass.REMIND:
            classified.query = (
                classified.query
                or classified.slots.get("attachment_title")
                or "insurance"
            )
    return classified


def _guess_entity(text: str) -> EntityType:
    if _INCIDENT_ENTITY.search(text):
        return EntityType.INCIDENT
    if _INVOICE_ENTITY.search(text):
        return EntityType.INVOICE
    if _DOC_ENTITY.search(text):
        return EntityType.DOCUMENT
    if _MEETING_ENTITY.search(text):
        return EntityType.MEETING
    if _REMINDER_ENTITY.search(text):
        return EntityType.REMINDER
    if _CATEGORY_ENTITY.search(text):
        return EntityType.CATEGORY
    if _EST_ENTITY.search(text):
        return EntityType.ESTABLISHMENT
    if _TASK_ENTITY.search(text):
        return EntityType.TASK
    if _STAFF_ENTITY.search(text):
        return EntityType.STAFF
    return EntityType.UNKNOWN


def _extract_query(text: str, intent: IntentClass, entity: EntityType) -> str:
    m = _TASK_TITLE.search(text)
    if m:
        for g in m.groups():
            if g and g.strip():
                q = g.strip(" .!?,")
                q = re.sub(r"^(the|a|an|mon|ma|mes)\s+", "", q, flags=re.I)
                q = re.sub(r"\s+task$", "", q, flags=re.I)
                if q.lower() not in ("it", "that", "this", "them"):
                    return q
    # "remind me about the insurance"
    m = re.search(r"(?:about|for|concerning)\s+(?:the\s+)?(.+?)(?:\s*[.!]|$)", text, re.I)
    if m and intent in (IntentClass.REMIND, IntentClass.RETRIEVE, IntentClass.QUERY):
        return m.group(1).strip(" .!?")
    # "show me the insurance document"
    m = re.search(r"(?:show|get|open)\s+(?:me\s+)?(?:the\s+)?(.+?)(?:\s+document)?$", text, re.I)
    if m and entity == EntityType.DOCUMENT:
        return re.sub(r"\s+document$", "", m.group(1).strip(), flags=re.I)
    return ""


def _base_confidence(
    intent: IntentClass,
    entity: EntityType,
    query: str,
    pronoun: bool,
    assignee: str,
    text: str,
) -> Confidence:
    if intent == IntentClass.UNKNOWN:
        return Confidence.LOW
    if pronoun and not query:
        return Confidence.MEDIUM  # needs working-set resolve
    if intent == IntentClass.COMPLETE and query:
        return Confidence.HIGH
    if intent == IntentClass.ASSIGN and assignee and (query or pronoun):
        return Confidence.HIGH if query else Confidence.MEDIUM
    if intent in (IntentClass.APPROVE, IntentClass.REJECT) and (query or pronoun):
        return Confidence.MEDIUM
    if intent == IntentClass.CREATE and entity != EntityType.UNKNOWN:
        return Confidence.MEDIUM
    if intent in (IntentClass.QUERY, IntentClass.RETRIEVE, IntentClass.SUMMARIZE):
        return Confidence.HIGH if query else Confidence.MEDIUM
    if len(text.split()) <= 2:
        return Confidence.LOW
    return Confidence.MEDIUM
