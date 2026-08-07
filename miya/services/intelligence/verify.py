"""Verification layer — DB re-read must match expected post-condition."""
from __future__ import annotations

from typing import Any, Callable

from miya.services.ops.result import OpsResult, fail, ok


def verify_mutation(
    *,
    operation: str,
    expected: dict[str, Any],
    fetch: Callable[[], dict[str, Any] | None],
    message_ok: str = "",
) -> OpsResult:
    """
    After a write, re-fetch and compare expected fields.

    ``expected`` keys are compared against the fetched dict (stringified).
    """
    row = None
    try:
        row = fetch()
    except Exception:
        row = None
    if not row:
        return fail(
            code="verify_failed",
            message="I couldn't verify the change in the database.",
            data={"operation": operation, "verified": False},
        )
    for key, want in (expected or {}).items():
        got = row.get(key)
        if str(got) != str(want):
            return fail(
                code="verify_failed",
                message=(
                    f"Verification failed for {operation}: "
                    f"expected {key}={want}, got {got}."
                ),
                data={
                    "operation": operation,
                    "verified": False,
                    "expected": expected,
                    "actual": row,
                },
            )
    return ok(
        message=message_ok or f"{operation} verified.",
        verified=True,
        data={"operation": operation, **row},
    )


def require_verified(result: OpsResult) -> OpsResult:
    """Downgrade success without verified=True."""
    if result.success and not result.verified and not result.needs_clarification:
        return fail(
            code="unverified",
            message=result.message_for_user
            or "The action could not be verified against the database.",
            data={**(result.data or {}), "operation": (result.data or {}).get("operation")},
        )
    return result
