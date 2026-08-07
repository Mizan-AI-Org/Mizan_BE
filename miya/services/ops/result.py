"""Canonical operational result envelope for Miya tools."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class OpsResult:
    """Strict success/failure contract — never imply Done unless success=True."""

    success: bool
    code: str = "ok"
    message_for_user: str = ""
    miya_directive: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    verified: bool = False
    needs_clarification: bool = False

    def as_tool_response(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "success": self.success,
            "code": self.code,
            "message_for_user": self.message_for_user,
            "verified": self.verified,
            "needs_clarification": self.needs_clarification,
            **self.data,
        }
        if self.miya_directive:
            payload["miya_directive"] = self.miya_directive
        if not self.success:
            payload.setdefault("error", self.code)
            # Hard rule for the model: do not claim the action succeeded.
            payload["miya_directive"] = (
                (self.miya_directive + " ") if self.miya_directive else ""
            ) + (
                "Do NOT tell the user the action succeeded. "
                "Relay message_for_user honestly. Never say 'Done' or 'I assigned…' "
                "unless success=true and verified=true."
            )
        elif self.needs_clarification:
            payload["miya_directive"] = (
                (self.miya_directive + " ") if self.miya_directive else ""
            ) + "Ask ONE clarifying question. Do not guess. Do not invent ids."
        else:
            payload.setdefault(
                "miya_directive",
                "Relay message_for_user. Cite refs/status from this payload only.",
            )
        return payload


def fail(
    *,
    code: str,
    message: str,
    miya_directive: str = "",
    data: dict[str, Any] | None = None,
    needs_clarification: bool = False,
) -> OpsResult:
    return OpsResult(
        success=False,
        code=code,
        message_for_user=message,
        miya_directive=miya_directive,
        data=data or {},
        verified=False,
        needs_clarification=needs_clarification,
    )


def ok(
    *,
    message: str,
    data: dict[str, Any] | None = None,
    verified: bool = False,
    code: str = "ok",
    miya_directive: str = "",
) -> OpsResult:
    return OpsResult(
        success=True,
        code=code,
        message_for_user=message,
        miya_directive=miya_directive,
        data=data or {},
        verified=verified,
        needs_clarification=False,
    )


def clarify(*, message: str, data: dict[str, Any] | None = None, code: str = "needs_clarification") -> OpsResult:
    return OpsResult(
        success=False,
        code=code,
        message_for_user=message,
        miya_directive="Ask for clarification. Do not execute until the entity is clear.",
        data=data or {},
        verified=False,
        needs_clarification=True,
    )
