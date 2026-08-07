"""Document Knowledge — OCR / compliance / tenant document facts.

Separate from conversation memory. Still below live DB entity status when conflicting.
"""
from __future__ import annotations

from miya.services.ops.context import OpsContext
from miya.services.ops.result import OpsResult, ok


def recall_document_knowledge(
    ctx: OpsContext,
    *,
    document_id: str = "",
    q: str = "",
) -> OpsResult:
    from miya.services.intelligence.reality import get_current_document
    from miya.services.ops.documents import query_document_intelligence

    if document_id or q:
        detail = get_current_document(ctx, document_id=document_id, q=q)
        if detail.success:
            data = dict(detail.data or {})
            data["layer"] = "DOCUMENT_DATA"
            data["authority"] = "DOCUMENT_DATA"
            return ok(
                message=detail.message_for_user,
                verified=detail.verified,
                data=data,
                miya_directive=(
                    "Use structured document fields. "
                    "If status of a related task/invoice conflicts, prefer CURRENT DATABASE STATE."
                ),
            )
        if detail.needs_clarification:
            return detail

    result = query_document_intelligence(ctx, q=q or document_id)
    if not result.success:
        return result
    data = dict(result.data or {})
    data["layer"] = "DOCUMENT_DATA"
    data["authority"] = "DOCUMENT_DATA"
    return ok(
        message=result.message_for_user,
        verified=result.verified,
        code=result.code,
        data=data,
        miya_directive=result.miya_directive,
    )
