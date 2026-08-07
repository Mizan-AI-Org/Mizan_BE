"""
Phase 14.3.2 — Document entity linking at reasoning time.

Deterministic resolution AFTER ingestion/extraction, BEFORE mutation decisions.
Read-only — never mutates DB or emits OperationalEvent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from django.db.models import Q

from miya.models import TenantDocument
from miya.services.document_versioning import get_document_versions, resolve_current_version
from miya.services.ops.context import OpsContext, guard_entity_location


class DocumentResolutionState(str, Enum):
    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_FOUND = "NOT_FOUND"
    NOT_APPLICABLE = "NOT_APPLICABLE"


# Signals that may deterministically select a mutation target (alone or in combination).
STRONG_SIGNALS = frozenset(
    {
        "explicit_id",
        "working_memory",
        "session_attachment",
        "document_family_id",
        "version_scope_resolved",
        "unique_category_vendor",
        "unique_category_establishment",
    }
)

WEAK_SIGNALS = frozenset({"filename", "recency", "category_only"})


@dataclass
class DocumentEntityResolution:
    state: DocumentResolutionState
    document_id: str = ""
    document_family_id: str = ""
    version_number: int = 0
    is_current: bool = False
    candidates: list[dict[str, Any]] = field(default_factory=list)
    clarify_message: str = ""
    evidence: list[str] = field(default_factory=list)
    source: str = ""
    version_scope: str = ""

    @property
    def needs_clarify(self) -> bool:
        return self.state == DocumentResolutionState.AMBIGUOUS

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "document_id": self.document_id or None,
            "document_family_id": self.document_family_id or None,
            "version_number": self.version_number or None,
            "is_current": self.is_current,
            "candidates": list(self.candidates),
            "clarify_message": self.clarify_message or None,
            "evidence": list(self.evidence),
            "source": self.source or None,
            "version_scope": self.version_scope or None,
        }


def parse_document_reference_hints(query: str = "", raw_message: str = "") -> dict[str, Any]:
    text = f"{query} {raw_message}".lower()
    hints: dict[str, Any] = {
        "version_scope": "",
        "category": "",
        "vendor": "",
        "wants_replace": False,
        "pronoun_this": False,
    }
    if re.search(r"\b(current|latest|most recent)\b", text):
        hints["version_scope"] = "current"
    elif re.search(r"\b(previous|prior|older|earlier|old)\b", text):
        hints["version_scope"] = "previous"
    elif re.search(r"\b(all versions|version history|every version|all version)\b", text):
        hints["version_scope"] = "all"
    if re.search(r"\b(insurance|assurance)\b", text):
        hints["category"] = "insurance"
    elif re.search(r"\b(compliance|haccp|certificate)\b", text):
        hints["category"] = "compliance"
    elif re.search(r"\b(invoice|facture|receipt)\b", text):
        hints["category"] = "invoice"
    if re.search(r"\b(replace|supersede|update)\b.+\b(document|certificate|policy|file)\b", text):
        hints["wants_replace"] = True
    if re.search(r"\b(this|that)\b.+\b(document|file|upload|certificate|policy)\b", text) or re.search(
        r"\bthe document i uploaded\b", text
    ):
        hints["pronoun_this"] = True
    vendor_m = re.search(r"\b(?:for|from|vendor|supplier)\s+([A-Za-z0-9][\w\s&.-]{1,40})", text, re.I)
    if vendor_m:
        hints["vendor"] = vendor_m.group(1).strip()
    return hints


def resolve_document_reference(
    ctx: OpsContext,
    *,
    document_id: str = "",
    document_family_id: str = "",
    query: str = "",
    raw_message: str = "",
    session_context: dict[str, Any] | None = None,
    version_scope: str = "",
    category: str = "",
    vendor: str = "",
    mutation_sensitive: bool = False,
    pronoun: bool = False,
) -> DocumentEntityResolution:
    """
    Resolve a TenantDocument reference for reasoning/planning.

    mutation_sensitive=True enforces strong-signal rules — filename/recency alone
    cannot select a mutation target.
    """
    sess = session_context or {}
    hints = parse_document_reference_hints(query=query, raw_message=raw_message)
    scope = (version_scope or hints.get("version_scope") or "").strip().lower()
    cat = (category or hints.get("category") or "").strip().lower()
    vend = (vendor or hints.get("vendor") or "").strip()
    use_pronoun = pronoun or bool(hints.get("pronoun_this"))

    # 1) Explicit document ID
    did = (document_id or sess.get("document_id") or "").strip()
    if not did:
        slots = sess.get("_document_input") or {}
        if isinstance(slots, dict) and slots.get("document_id"):
            did = str(slots["document_id"])

    if did:
        doc = _scoped_queryset(ctx).filter(id=did).first()
        if doc is None:
            return DocumentEntityResolution(
                state=DocumentResolutionState.NOT_FOUND,
                clarify_message="I couldn't find that document in your workspace.",
                evidence=["explicit_id"],
            )
        loc_err = guard_entity_location(ctx, doc)
        if loc_err:
            return DocumentEntityResolution(
                state=DocumentResolutionState.NOT_FOUND,
                clarify_message=loc_err.message_for_user or "That document isn't in your establishment scope.",
                evidence=["explicit_id", "establishment_forbidden"],
            )
        resolved = _resolve_version_in_family(ctx, doc, scope=scope or "specific")
        resolved.evidence.append("explicit_id")
        resolved.source = "explicit_id"
        return resolved

    # 2) Explicit document family + version scope
    family_id = (document_family_id or "").strip()
    if family_id:
        family_res = _resolve_family_scope(ctx, family_id, scope=scope or "current")
        if family_res.state == DocumentResolutionState.RESOLVED:
            family_res.evidence.append("document_family_id")
            family_res.source = "document_family_id"
        return family_res

    # 3) Working memory / session attachment (pronoun or "this document")
    wm_doc = _from_working_memory_document(ctx)
    attachment_ids = _session_attachment_ids(sess)
    if use_pronoun or not query:
        if wm_doc:
            doc = _scoped_queryset(ctx).filter(id=wm_doc).first()
            if doc:
                resolved = _resolve_version_in_family(ctx, doc, scope=scope or "current")
                resolved.evidence.append("working_memory")
                resolved.source = "working_memory"
                return resolved
        if len(attachment_ids) == 1:
            doc = _scoped_queryset(ctx).filter(id=attachment_ids[0]).first()
            if doc:
                resolved = _resolve_version_in_family(ctx, doc, scope=scope or "current")
                resolved.evidence.append("session_attachment")
                resolved.source = "session_attachment"
                return resolved
        if use_pronoun and len(attachment_ids) > 1:
            cands = [_candidate_row(_scoped_queryset(ctx).filter(id=i).first()) for i in attachment_ids]
            cands = [c for c in cands if c]
            return DocumentEntityResolution(
                state=DocumentResolutionState.AMBIGUOUS,
                candidates=cands,
                clarify_message=_clarify_message(cands, prefix="Which attached document do you mean?"),
                evidence=["session_attachment"],
            )

    # 4) Version scope without family — search current heads by category
    if scope in ("current", "previous", "all") and cat:
        return _resolve_by_category_and_scope(ctx, category=cat, scope=scope, vendor=vend, mutation_sensitive=mutation_sensitive)

    # 5) DB candidate search
    return _resolve_from_candidates(
        ctx,
        query=query or raw_message,
        category=cat,
        vendor=vend,
        mutation_sensitive=mutation_sensitive,
        scope=scope,
        session_context=sess,
        wm_doc=wm_doc,
        attachment_ids=attachment_ids,
    )


def _valid_restaurant_id(restaurant_id: str) -> bool:
    import uuid

    try:
        uuid.UUID(str(restaurant_id))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _scoped_queryset(ctx: OpsContext):
    from miya.services.ops.scoping import apply_location_scope, filter_visible_location_ids

    if not _valid_restaurant_id(ctx.restaurant_id):
        return TenantDocument.objects.none()

    qs = TenantDocument.objects.filter(restaurant_id=ctx.restaurant_id)
    if ctx.location_id:
        qs = apply_location_scope(qs, location_id=ctx.location_id, field="location_id", allow_null=True)
    elif len(ctx.available_locations or []) > 1:
        qs = filter_visible_location_ids(
            qs,
            location_ids=[str(r.get("id")) for r in ctx.available_locations if r.get("id")],
            field="location_id",
        )
    return qs


def _from_working_memory_document(ctx: OpsContext) -> str:
    try:
        from miya.services.intelligence.working_memory import get_working_memory

        wm = get_working_memory(user=ctx.user, restaurant=ctx.restaurant)
        return str(wm.get("current_document_id") or "")
    except Exception:
        return ""


def _session_attachment_ids(session_context: dict[str, Any]) -> list[str]:
    ids = list(session_context.get("attachment_ids") or [])
    doc_in = session_context.get("_document_input") or {}
    if isinstance(doc_in, dict) and doc_in.get("document_id"):
        ids.append(str(doc_in["document_id"]))
    out: list[str] = []
    for i in ids:
        s = str(i).strip()
        if s and s not in out:
            out.append(s)
    return out


def _resolve_family_scope(
    ctx: OpsContext,
    document_family_id: str,
    *,
    scope: str,
) -> DocumentEntityResolution:
    versions = get_document_versions(str(ctx.restaurant_id), document_family_id)
    versions = [v for v in versions if _doc_in_scope(ctx, v)]
    if not versions:
        return DocumentEntityResolution(
            state=DocumentResolutionState.NOT_FOUND,
            clarify_message="I couldn't find that document family in your workspace.",
            version_scope=scope,
        )
    if scope == "all":
        current = next((v for v in versions if v.is_current), versions[-1])
        return DocumentEntityResolution(
            state=DocumentResolutionState.RESOLVED,
            document_id=str(current.id),
            document_family_id=document_family_id,
            version_number=current.version_number,
            is_current=current.is_current,
            candidates=[_candidate_row(v) for v in versions],
            evidence=["version_scope_resolved"],
            source="document_family_id",
            version_scope="all",
        )
    if scope == "previous":
        current = next((v for v in reversed(versions) if v.is_current), versions[-1])
        prior = [v for v in versions if v.version_number < current.version_number]
        if not prior:
            return DocumentEntityResolution(
                state=DocumentResolutionState.NOT_FOUND,
                clarify_message="There is no previous version of that document.",
                document_family_id=document_family_id,
                version_scope="previous",
            )
        prev = prior[-1]
        return DocumentEntityResolution(
            state=DocumentResolutionState.RESOLVED,
            document_id=str(prev.id),
            document_family_id=document_family_id,
            version_number=prev.version_number,
            is_current=False,
            evidence=["version_scope_resolved"],
            source="document_family_id",
            version_scope="previous",
        )
    # current (default)
    current = next((v for v in reversed(versions) if v.is_current), None)
    if current is None:
        current = resolve_current_version(versions[-1])
    return DocumentEntityResolution(
        state=DocumentResolutionState.RESOLVED,
        document_id=str(current.id),
        document_family_id=document_family_id,
        version_number=current.version_number,
        is_current=True,
        evidence=["version_scope_resolved"],
        source="document_family_id",
        version_scope="current",
    )


def _resolve_version_in_family(
    ctx: OpsContext,
    doc: TenantDocument,
    *,
    scope: str,
) -> DocumentEntityResolution:
    family_id = str(doc.document_family_id or doc.id)
    if scope in ("current", ""):
        head = resolve_current_version(doc)
        return DocumentEntityResolution(
            state=DocumentResolutionState.RESOLVED,
            document_id=str(head.id),
            document_family_id=family_id,
            version_number=head.version_number,
            is_current=head.is_current,
            evidence=["version_scope_resolved"],
            version_scope="current",
        )
    if scope == "specific":
        return DocumentEntityResolution(
            state=DocumentResolutionState.RESOLVED,
            document_id=str(doc.id),
            document_family_id=family_id,
            version_number=doc.version_number,
            is_current=doc.is_current,
            version_scope="specific",
        )
    return _resolve_family_scope(ctx, family_id, scope=scope)


def _resolve_by_category_and_scope(
    ctx: OpsContext,
    *,
    category: str,
    scope: str,
    vendor: str,
    mutation_sensitive: bool,
) -> DocumentEntityResolution:
    qs = _scoped_queryset(ctx)
    families = _distinct_current_families(qs, category=category, vendor=vendor)
    if not families:
        return DocumentEntityResolution(
            state=DocumentResolutionState.NOT_FOUND,
            clarify_message=f"I couldn't find a {category or 'matching'} document in your workspace.",
            evidence=["category_only"] if category else [],
            version_scope=scope,
        )
    if len(families) > 1:
        cands = [_candidate_row(d) for d in families]
        return DocumentEntityResolution(
            state=DocumentResolutionState.AMBIGUOUS,
            candidates=cands,
            clarify_message=_clarify_message(cands, prefix=f"I found several {category} documents."),
            evidence=["category_only"],
            version_scope=scope,
        )
    return _resolve_family_scope(ctx, str(families[0].document_family_id or families[0].id), scope=scope)


def _resolve_from_candidates(
    ctx: OpsContext,
    *,
    query: str,
    category: str,
    vendor: str,
    mutation_sensitive: bool,
    scope: str,
    session_context: dict[str, Any],
    wm_doc: str,
    attachment_ids: list[str],
) -> DocumentEntityResolution:
    qs = _scoped_queryset(ctx)
    if scope != "all":
        qs = qs.filter(is_current=True)

    needle = (query or "").strip().lower()
    scored: list[tuple[int, list[str], TenantDocument]] = []

    for doc in qs.order_by("-created_at")[:80]:
        score, evidence = _score_candidate(
            doc,
            needle=needle,
            category=category,
            vendor=vendor,
            wm_doc=wm_doc,
            attachment_ids=attachment_ids,
        )
        if score > 0 or not needle:
            scored.append((score, evidence, doc))

    if not scored and needle:
        return DocumentEntityResolution(
            state=DocumentResolutionState.NOT_FOUND,
            clarify_message=f"I couldn't find a document matching '{query.strip()}'.",
        )

    if not scored:
        return DocumentEntityResolution(
            state=DocumentResolutionState.NOT_FOUND,
            clarify_message="Which document do you mean? Tell me the title, type, or upload date.",
        )

    scored.sort(key=lambda row: (-row[0], -row[2].created_at.timestamp() if row[2].created_at else 0))
    top_score, top_evidence, top_doc = scored[0]
    strong = [e for e in top_evidence if e in STRONG_SIGNALS]
    weak_only = top_evidence and not strong and all(e in WEAK_SIGNALS for e in top_evidence)

    if len(scored) == 1:
        if mutation_sensitive and weak_only:
            return DocumentEntityResolution(
                state=DocumentResolutionState.AMBIGUOUS,
                candidates=[_candidate_row(top_doc)],
                clarify_message=(
                    "I need a clearer reference before I change anything — "
                    "which document do you mean? Give me the title, type, or document id."
                ),
                evidence=top_evidence,
            )
        resolved = _resolve_version_in_family(ctx, top_doc, scope=scope or "current")
        resolved.evidence.extend(top_evidence)
        resolved.source = "database"
        return resolved

    second_score = scored[1][0]
    if top_score == second_score or (top_score - second_score < 2 and len(scored) > 1):
        cands = [_candidate_row(row[2]) for row in scored[:5]]
        return DocumentEntityResolution(
            state=DocumentResolutionState.AMBIGUOUS,
            candidates=cands,
            clarify_message=_clarify_message(cands),
            evidence=top_evidence,
        )

    if mutation_sensitive and not strong:
        cands = [_candidate_row(row[2]) for row in scored[:5]]
        return DocumentEntityResolution(
            state=DocumentResolutionState.AMBIGUOUS,
            candidates=cands,
            clarify_message=_clarify_message(
                cands,
                prefix="I found several possible documents and won't pick one silently.",
            ),
            evidence=top_evidence,
        )

    resolved = _resolve_version_in_family(ctx, top_doc, scope=scope or "current")
    resolved.evidence.extend(top_evidence)
    resolved.source = "database"
    return resolved


def _score_candidate(
    doc: TenantDocument,
    *,
    needle: str,
    category: str,
    vendor: str,
    wm_doc: str,
    attachment_ids: list[str],
) -> tuple[int, list[str]]:
    score = 0
    evidence: list[str] = []
    title = (doc.title or "").lower()
    fname = (doc.original_filename or "").lower()
    summary = (doc.summary or "").lower()
    cat = (doc.category or "").lower()
    structured = doc.structured_fields if isinstance(doc.structured_fields, dict) else {}
    doc_vendor = (structured.get("vendor") or doc.vendor_name or "").lower()

    if str(doc.id) == wm_doc:
        score += 10
        evidence.append("working_memory")
    if str(doc.id) in attachment_ids:
        score += 10
        evidence.append("session_attachment")

    if category:
        blob = f"{title} {summary} {cat} {doc.tags}"
        if category in blob.lower():
            score += 4
            evidence.append("category_only")

    if vendor and vendor.lower() in doc_vendor:
        score += 5
        if category and category in cat:
            evidence.append("unique_category_vendor")
        else:
            evidence.append("category_only")

    if needle:
        if needle in title:
            score += 3
            evidence.append("category_only")
        if needle in fname:
            score += 1
            evidence.append("filename")
        if needle in summary or needle in cat:
            score += 2
            evidence.append("category_only")
        if needle in doc_vendor:
            score += 4
            evidence.append("unique_category_vendor")

    if doc.is_current:
        score += 1

    return score, list(dict.fromkeys(evidence))


def _distinct_current_families(qs, *, category: str, vendor: str) -> list[TenantDocument]:
    rows: list[TenantDocument] = []
    seen: set[str] = set()
    for doc in qs.filter(is_current=True).order_by("-created_at"):
        blob = f"{doc.title} {doc.summary} {doc.category}".lower()
        if category and category not in blob and category not in str(doc.tags).lower():
            continue
        structured = doc.structured_fields if isinstance(doc.structured_fields, dict) else {}
        doc_vendor = (structured.get("vendor") or doc.vendor_name or "").lower()
        if vendor and vendor.lower() not in doc_vendor:
            continue
        fam = str(doc.document_family_id or doc.id)
        if fam in seen:
            continue
        seen.add(fam)
        rows.append(doc)
    return rows


def _doc_in_scope(ctx: OpsContext, doc: TenantDocument) -> bool:
    if guard_entity_location(ctx, doc) is not None:
        return False
    return True


def _candidate_row(doc: TenantDocument | None) -> dict[str, Any]:
    if doc is None:
        return {}
    return {
        "id": str(doc.id),
        "title": doc.title,
        "category": doc.category,
        "original_filename": doc.original_filename,
        "version_number": doc.version_number,
        "is_current": doc.is_current,
        "document_family_id": str(doc.document_family_id or doc.id),
        "uploaded_at": doc.created_at.isoformat() if doc.created_at else None,
        "location_id": str(doc.location_id) if doc.location_id else None,
    }


def _clarify_message(candidates: list[dict[str, Any]], *, prefix: str = "I found several documents.") -> str:
    if not candidates:
        return f"{prefix} Which one do you mean?"
    parts = []
    for c in candidates[:4]:
        label = c.get("title") or c.get("original_filename") or "document"
        when = c.get("uploaded_at") or ""
        if when:
            parts.append(f"*{label}* (uploaded {when[:10]})")
        else:
            parts.append(f"*{label}*")
    joined = " or ".join(parts)
    return f"{prefix} Do you mean {joined}? I won't guess — tell me which one."


def document_resolution_to_entity(res: DocumentEntityResolution):
    """Bridge Phase 14.3.2 document linking → legacy EntityResolution."""
    from miya.services.intelligence.entity_resolver import EntityResolution

    if res.state == DocumentResolutionState.RESOLVED:
        return EntityResolution(
            entity_type="document",
            entity_id=res.document_id,
            candidates=res.candidates or None,
            source=res.source,
        )
    if res.state == DocumentResolutionState.AMBIGUOUS:
        msg = res.clarify_message or "Which document do you mean?"
        if "won't guess" not in msg.lower() and "guess" not in msg.lower():
            msg = f"{msg.rstrip('.')} — I won't guess."
        return EntityResolution(
            entity_type="document",
            candidates=res.candidates,
            clarify_message=msg,
        )
    if res.state == DocumentResolutionState.NOT_FOUND:
        return EntityResolution(
            entity_type="document",
            clarify_message=res.clarify_message or "I couldn't find that document.",
        )
    return EntityResolution(entity_type="document")
