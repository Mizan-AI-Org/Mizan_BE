"""
Phase 11 — universal mutation verification pipeline.

Hard rule: no mutation returns ``success=True`` unless ``verified=True``.
"""
from __future__ import annotations

from typing import Any

from miya.services.intelligence.mutation_registry import (
    canonical_target,
    is_legacy_http_mutation,
    is_mutation_tool,
)
from miya.services.intelligence.verify import require_verified
from miya.services.ops.result import OpsResult, fail


def ensure_ops_mutation_verified(tool_name: str, result: OpsResult | None) -> OpsResult | None:
    """Apply ``require_verified`` to canonical/structured mutation results."""
    if result is None or not is_mutation_tool(tool_name):
        return result
    return require_verified(result)


def enforce_mutation_tool_response(tool_name: str, body: dict[str, Any]) -> dict[str, Any]:
    """
    Final gate on tool responses already shaped as dicts (canonical / structured path).
    """
    if not isinstance(body, dict) or not is_mutation_tool(tool_name):
        return body
    if body.get("needs_clarification"):
        return body
    if body.get("success") is True and body.get("verified") is not True:
        downgraded = require_verified(
            OpsResult(
                success=True,
                code=str(body.get("code") or "ok"),
                message_for_user=str(body.get("message_for_user") or ""),
                data={
                    k: v
                    for k, v in body.items()
                    if k not in ("success", "verified", "code", "needs_clarification", "miya_directive")
                },
                verified=bool(body.get("verified")),
                needs_clarification=bool(body.get("needs_clarification")),
            )
        )
        return downgraded.as_tool_response()
    return body


def finalize_legacy_tool_response(
    tool_name: str,
    *,
    status_code: int,
    body: Any,
) -> dict[str, Any]:
    """
    Legacy HTTP path — fail closed for unverified mutations.
    Read tools and deferred OCR/admin tools pass through unchanged.
    """
    if status_code >= 400:
        from miya.services.user_errors import pick_user_message, sanitize_user_error

        body_dict = body if isinstance(body, dict) else {}
        user_msg = pick_user_message(body_dict)
        return {
            "success": False,
            "status_code": status_code,
            "error": sanitize_user_error(body_dict.get("error") or user_msg),
            "message_for_user": user_msg,
            "verified": False,
            "details": body,
        }

    if isinstance(body, dict) and body.get("message_for_user"):
        from miya.services.user_errors import sanitize_user_error

        body = {**body, "message_for_user": sanitize_user_error(body["message_for_user"])}

    if isinstance(body, dict):
        from miya.services.reply_format import sanitize_tool_payload_for_llm

        body = sanitize_tool_payload_for_llm(body)

    if not is_mutation_tool(tool_name):
        return {"success": True, "data": body}

    if isinstance(body, dict) and body.get("verified") is True:
        return {"success": True, "verified": True, **body}

    if is_legacy_http_mutation(tool_name):
        target = canonical_target(tool_name)
        return fail(
            code="legacy_unverified_mutation",
            message=(
                "That action isn't available through the verified pipeline yet. "
                "I've blocked it rather than claim success without database confirmation."
            ),
            miya_directive=(
                "Do NOT tell the user the action succeeded. "
                f"Migration target: {target or 'canonical ops handler'}."
            ),
            data={
                "tool": tool_name,
                "migration_target": target,
                "legacy_body": body if isinstance(body, dict) else {"raw": str(body)[:500]},
            },
        ).as_tool_response()

    # Canonical/structured mutations must never reach legacy HTTP.
    return fail(
        code="routing_gap",
        message="Internal routing error — mutation did not use the verified ops layer.",
        data={"tool": tool_name},
    ).as_tool_response()
