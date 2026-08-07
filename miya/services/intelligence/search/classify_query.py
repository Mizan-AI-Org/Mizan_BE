"""Parse NL into search mode + domain + metadata filters."""
from __future__ import annotations

import re
from typing import Any

from miya.services.intelligence.search.concepts import expand_concepts
from miya.services.intelligence.search.types import (
    ParsedSearchQuery,
    SearchDomain,
    SearchFilters,
    SearchMode,
)

_FIND = re.compile(
    r"\b(find|show|get|list|search|look\s*up|retrieve|display|affiche|montre|cherche)\b",
    re.I,
)
_EVENTISH = re.compile(
    r"\b(what\s+happened|who\s+handled|who\s+(?:dealt|took\s+care)|"
    r"history|timeline|what\s+went\s+on|follow[- ]?up\s+on)\b",
    re.I,
)
_CONCEPTUAL = re.compile(
    r"\b(where\s+someone|complained|complaining|about\s+the|related\s+to|regarding|"
    r"kind\s+of|similar\s+to|like\s+the|something\s+about|"
    r"mentioned|talking\s+about|reported\s+about|story)\b",
    re.I,
)
_STRUCTURED_VENDOR = re.compile(
    r"\b(?:invoices?|bills?|factures?)\s+(?:from|by|for)\s+([A-Za-z0-9][A-Za-z0-9 &.'\-]{1,60})",
    re.I,
)
_STRUCTURED_NAMED = re.compile(
    r"\b(?:find|show|get)\s+(?:the\s+)?([a-z0-9][a-z0-9 \-]{1,40}?)\s+(incident|invoice|task|document|checklist)\b",
    re.I,
)
_WHO_STAFF = re.compile(
    r"\bwhich\s+staff\b|\bwho\s+(?:hasn'?t|have\s+not|didn'?t)\b|\bstaff\s+who\b",
    re.I,
)
_DAYS = re.compile(r"\b(last\s+week|yesterday|today|this\s+week|last\s+(\d+)\s+days?)\b", re.I)
_STATUS = re.compile(
    r"\b(open|closed|resolved|pending|completed|overdue|uncompleted|incomplete)\b",
    re.I,
)


def parse_search_query(
    message: str,
    *,
    session_context: dict[str, Any] | None = None,
) -> ParsedSearchQuery:
    text = (message or "").strip()
    ctx = session_context or {}
    filters = SearchFilters(
        organization_id=str(ctx.get("restaurant_id") or ""),
        establishment_id=str(ctx.get("location_id") or ""),
        q="",
    )
    reasons: list[str] = []
    domain = SearchDomain.UNKNOWN
    mode = SearchMode.STRUCTURED

    # Domain detection
    low = text.lower()
    if _WHO_STAFF.search(text) or ("checklist" in low and "staff" in low):
        domain = SearchDomain.CHECKLIST if "checklist" in low else SearchDomain.STAFF
        reasons.append("staff_or_checklist")
    elif re.search(r"\bchecklist", low):
        domain = SearchDomain.CHECKLIST
        reasons.append("checklist_domain")
    elif re.search(r"\bmeeting|calendar\b", low):
        domain = SearchDomain.MEETING
        reasons.append("meeting_domain")
    elif re.search(r"\bstaff\b|\bemployee\b", low) and not re.search(r"\bincident\b", low):
        domain = SearchDomain.STAFF
        reasons.append("staff_domain")
    elif re.search(r"\binsurance\b|\bdocument", low):
        domain = SearchDomain.DOCUMENT
        reasons.append("document_domain")
    elif re.search(r"\binvoice|facture|bill\b", low):
        domain = SearchDomain.INVOICE
        reasons.append("invoice_domain")
    elif re.search(
        r"\bincident|accident|freezer|fridge|frigo|safety|complaint|complain|"
        r"hazard|congel",
        low,
    ):
        domain = SearchDomain.INCIDENT
        reasons.append("incident_domain")
    elif re.search(r"\btask|demande|tâche|tache\b", low):
        domain = SearchDomain.TASK
        reasons.append("task_domain")
    elif re.search(r"\bphoto?s\b", low) and (
        re.search(r"\b\w+'s\s+photo", low) or re.search(r"\b(?:maxime|closing|opening)\b", low)
    ):
        domain = SearchDomain.TASK
        reasons.append("task_photos_domain")
    elif re.search(r"\bdelivery|livraison\b", low):
        domain = SearchDomain.MIXED
        reasons.append("delivery_mixed")
    elif _EVENTISH.search(text):
        domain = SearchDomain.EVENT
        reasons.append("event_domain")

    # Mode
    if _EVENTISH.search(text) or domain == SearchDomain.EVENT:
        mode = SearchMode.EVENT
        reasons.append("event_mode")
        if domain == SearchDomain.UNKNOWN:
            # "What happened with the late delivery?" → event + conceptual
            if "delivery" in low or "incident" in low:
                domain = SearchDomain.INCIDENT if "incident" in low else SearchDomain.MIXED
                mode = SearchMode.HYBRID
                reasons.append("event_hybrid")
    elif _CONCEPTUAL.search(text) or _is_paraphrase(text, domain):
        mode = SearchMode.HYBRID if domain != SearchDomain.UNKNOWN else SearchMode.SEMANTIC
        reasons.append("conceptual_mode")
    elif _FIND.search(text) or domain != SearchDomain.UNKNOWN:
        mode = SearchMode.STRUCTURED
        reasons.append("structured_mode")
    else:
        mode = SearchMode.SEMANTIC
        reasons.append("fallback_semantic")

    # Metadata filters
    vm = _STRUCTURED_VENDOR.search(text)
    if vm:
        filters.vendor = vm.group(1).strip(" .!?")
        filters.q = filters.vendor
        domain = SearchDomain.INVOICE
        mode = SearchMode.STRUCTURED
        reasons.append("vendor_filter")

    nm = _STRUCTURED_NAMED.search(text)
    if nm and not filters.vendor:
        filters.q = nm.group(1).strip()
        ent = nm.group(2).lower()
        domain = {
            "incident": SearchDomain.INCIDENT,
            "invoice": SearchDomain.INVOICE,
            "task": SearchDomain.TASK,
            "document": SearchDomain.DOCUMENT,
            "checklist": SearchDomain.CHECKLIST,
        }.get(ent, domain)
        reasons.append("named_entity_filter")

    sm = _STATUS.search(text)
    if sm:
        filters.status = _normalize_status(sm.group(1), domain)
        reasons.append("status_filter")

    dm = _DAYS.search(text)
    if dm:
        phrase = dm.group(0).lower()
        if "yesterday" in phrase:
            filters.since = "yesterday"
            filters.days = 1
        elif "today" in phrase:
            filters.days = 1
            filters.since = "today"
        elif "last week" in phrase or "this week" in phrase:
            filters.days = 7
        elif dm.group(2):
            filters.days = int(dm.group(2))
        reasons.append("date_filter")

    if not filters.q:
        filters.q = _extract_needle(text, domain)

    filters.conceptual_terms = expand_concepts(text)
    if mode in (SearchMode.SEMANTIC, SearchMode.HYBRID) and not filters.conceptual_terms:
        filters.conceptual_terms = expand_concepts(filters.q)

    confidence = "HIGH" if len(reasons) >= 2 and domain != SearchDomain.UNKNOWN else "MEDIUM"
    if domain == SearchDomain.UNKNOWN:
        confidence = "LOW"

    return ParsedSearchQuery(
        raw=text,
        mode=mode,
        domain=domain,
        filters=filters,
        reasons=reasons,
        confidence=confidence,
    )


def _is_paraphrase(text: str, domain: SearchDomain) -> bool:
    """Longer descriptive queries without a crisp ID/title → conceptual."""
    words = text.split()
    if len(words) >= 8 and domain in (SearchDomain.INCIDENT, SearchDomain.MIXED, SearchDomain.UNKNOWN):
        return True
    if "complained" in text.lower() or "related to" in text.lower():
        return True
    return False


def _normalize_status(raw: str, domain: SearchDomain) -> str:
    r = (raw or "").lower()
    if r in ("open",):
        return "OPEN"
    if r in ("closed", "resolved"):
        return "RESOLVED" if domain == SearchDomain.INCIDENT else "COMPLETED"
    if r in ("pending",):
        return "PENDING"
    if r in ("completed",):
        return "COMPLETED"
    if r in ("overdue", "uncompleted", "incomplete"):
        return "OPEN"
    return r.upper()


def _extract_needle(text: str, domain: SearchDomain) -> str:
    t = text.strip()
    t = re.sub(
        r"^(find|show|get|list|search|look\s*up|display|tell\s+me|what|which|who)\s+",
        "",
        t,
        flags=re.I,
    )
    t = re.sub(r"\b(me|the|a|an)\s+", " ", t, flags=re.I)
    t = re.sub(
        r"\b(incident|invoice|task|document|checklist|staff|insurance)s?\b",
        " ",
        t,
        flags=re.I,
    )
    t = re.sub(r"\s+", " ", t).strip(" .!?")
    # Keep useful short needles like "freezer" / "ABC Foods"
    if len(t) > 80:
        t = t[:80]
    return t
