"""Structured DB retrieval via canonical ops find_* (scoped + permissioned)."""
from __future__ import annotations

from typing import Any

from miya.services.intelligence.search.types import ParsedSearchQuery, SearchDomain, SearchHit
from miya.services.ops.context import OpsContext
from miya.services.ops.result import OpsResult


def structured_search(ctx: OpsContext, parsed: ParsedSearchQuery) -> tuple[list[SearchHit], list[str]]:
    """Run the appropriate find_* — never bypass OpsContext scoping."""
    strategy: list[str] = []
    f = parsed.filters
    domain = parsed.domain

    if domain == SearchDomain.INCIDENT:
        strategy.append("find_incidents")
        return _incidents(ctx, f.q, f.status, f.days, f.since), strategy
    if domain == SearchDomain.INVOICE:
        strategy.append("find_invoices")
        return _invoices(ctx, f.q or f.vendor, f.vendor, f.status, f.days), strategy
    if domain == SearchDomain.TASK:
        strategy.append("find_tasks")
        return _tasks(ctx, f.q, f.status), strategy
    if domain == SearchDomain.STAFF:
        strategy.append("find_staff")
        return _staff(ctx, f.q or f.staff_name), strategy
    if domain == SearchDomain.DOCUMENT:
        strategy.append("find_documents")
        return _documents(ctx, f.q or "insurance", f.category or "insurance"), strategy
    if domain == SearchDomain.CHECKLIST:
        strategy.append("find_tasks_checklist")
        return _checklists(ctx, f.q or "opening checklist"), strategy
    if domain == SearchDomain.MEETING:
        strategy.append("list_meetings")
        return _meetings(ctx, f.q), strategy
    if domain == SearchDomain.MIXED:
        strategy.append("mixed_structured")
        hits = []
        hits.extend(_incidents(ctx, f.q, f.status, f.days, f.since)[:5])
        hits.extend(_tasks(ctx, f.q, f.status)[:5])
        hits.extend(_invoices(ctx, f.q, f.vendor, f.status, f.days)[:5])
        return hits, strategy

    # Unknown domain: light multi-domain structured pass (still scoped)
    strategy.append("multi_domain_structured")
    hits = []
    hits.extend(_incidents(ctx, f.q, "", f.days, f.since)[:4])
    hits.extend(_tasks(ctx, f.q, "")[:4])
    hits.extend(_invoices(ctx, f.q, f.vendor, "", f.days)[:4])
    hits.extend(_documents(ctx, f.q, "")[:4])
    hits.extend(_staff(ctx, f.q)[:4])
    return hits, strategy


def _incidents(ctx, q, status, days, since) -> list[SearchHit]:
    from miya.services.ops.incidents import find_incidents

    result = find_incidents(
        ctx,
        q=q or "",
        status=status or ("ALL" if q else "OPEN"),
        days=days,
        since=since or "",
        limit=20,
    )
    return _from_ops(result, "incident", "incidents", title_keys=("title", "incident_type"))


def _invoices(ctx, q, vendor, status, days) -> list[SearchHit]:
    from miya.services.ops.invoices import find_invoices

    result = find_invoices(
        ctx,
        q=q or vendor or "",
        vendor=vendor or "",
        status=status or "",
        days=days,
        limit=20,
    )
    return _from_ops(result, "invoice", "invoices", title_keys=("vendor", "vendor_name", "title"))


def _tasks(ctx, q, status) -> list[SearchHit]:
    from miya.services.ops.tasks import find_tasks

    result = find_tasks(ctx, q=q or "", status=status or "", limit=20)
    return _from_ops(result, "task", "tasks", title_keys=("title",))


def _staff(ctx, q) -> list[SearchHit]:
    from miya.services.ops.staff import find_staff

    result = find_staff(ctx, q=q or "", limit=20)
    return _from_ops(result, "staff", "staff", title_keys=("name", "email"))


def _documents(ctx, q, kind) -> list[SearchHit]:
    from miya.services.ops.documents import find_documents

    result = find_documents(ctx, q=q or "", kind=kind or "", limit=20)
    return _from_ops(result, "document", "documents", title_keys=("title", "category"))


def _checklists(ctx, q) -> list[SearchHit]:
    from miya.services.ops.tasks import find_tasks

    result = find_tasks(ctx, q=q or "checklist", status="OPEN", limit=30)
    hits = _from_ops(result, "checklist", "tasks", title_keys=("title",))
    # Prefer opening checklist rows
    ranked = sorted(
        hits,
        key=lambda h: (
            0 if "opening" in (h.title or "").lower() or "checklist" in (h.title or "").lower() else 1,
            -h.score,
        ),
    )
    return ranked


def _meetings(ctx, q) -> list[SearchHit]:
    from miya.services.ops.meetings import list_meetings

    result = list_meetings(ctx, q=q or "", days=7, limit=20)
    return _from_ops(result, "meeting", "meetings", title_keys=("title",))


def _from_ops(
    result: OpsResult,
    domain: str,
    key: str,
    *,
    title_keys: tuple[str, ...],
) -> list[SearchHit]:
    if not result.success:
        return []
    rows = (result.data or {}).get(key) or []
    hits: list[SearchHit] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = ""
        for k in title_keys:
            if row.get(k):
                title = str(row[k])
                break
        hits.append(
            SearchHit(
                domain=domain,
                id=str(row.get("id") or ""),
                title=title or domain,
                snippet=str(row.get("description") or row.get("summary") or row.get("status") or "")[:200],
                score=0.7,
                source="structured",
                metadata=row,
            )
        )
    return hits
