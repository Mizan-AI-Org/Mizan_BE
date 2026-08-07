"""In-process agent mutation bridge with DB verification — Phase 11 Wave 1."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from core.agent_auth import primary_agent_bearer_token
from miya.services.ops.context import OpsContext, guard_entity_location, require_permission, require_restaurant
from miya.services.ops.result import OpsResult, fail, ok

logger = logging.getLogger(__name__)


def enrich_agent_payload(ctx: OpsContext, args: dict[str, Any] | None) -> dict[str, Any]:
    """Merge session tenant/user context into agent payloads."""
    payload = dict(args or {})
    payload.setdefault("restaurant_id", ctx.restaurant_id)
    payload.setdefault("restaurantId", ctx.restaurant_id)
    payload.setdefault("user_id", ctx.user_id)
    payload.setdefault("userId", ctx.user_id)
    if ctx.location_id:
        payload.setdefault("location_id", ctx.location_id)
        payload.setdefault("locationId", ctx.location_id)
    phone = getattr(ctx.user, "phone", None)
    if phone and not payload.get("phone"):
        payload.setdefault("phone", phone)
    channel = ctx.channel or "dashboard"
    payload.setdefault("channel", channel)
    payload.setdefault("delivery_channel", channel)
    return payload


def agent_headers(ctx: OpsContext) -> dict[str, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    key = primary_agent_bearer_token()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    headers["X-Restaurant-Id"] = str(ctx.restaurant_id)
    if ctx.location_id:
        headers["X-Location-Id"] = str(ctx.location_id)
    return headers


def dispatch_agent_post(
    ctx: OpsContext,
    path: str,
    payload: dict[str, Any],
    *,
    method: str = "POST",
) -> tuple[int, dict[str, Any]]:
    from rest_framework.test import APIRequestFactory
    from django.urls import resolve
    import json

    factory = APIRequestFactory()
    hdrs = agent_headers(ctx)
    method_upper = (method or "POST").upper()

    if method_upper == "GET":
        request = factory.get(path, payload, format="json")
    elif method_upper == "PATCH":
        request = factory.patch(path, payload, format="json")
    elif method_upper == "PUT":
        request = factory.put(path, payload, format="json")
    else:
        request = factory.post(path, payload, format="json")

    for name, value in hdrs.items():
        if not value:
            continue
        if name.lower() == "authorization":
            request.META["HTTP_AUTHORIZATION"] = value
        elif name.lower() == "content-type":
            request.META["CONTENT_TYPE"] = value
        else:
            request.META["HTTP_" + name.upper().replace("-", "_")] = value

    match = resolve(path)
    response = match.func(request, *match.args, **match.kwargs)
    if hasattr(response, "data"):
        body = response.data
    else:
        try:
            raw = response.content.decode() if response.content else ""
            body = json.loads(raw) if raw else {}
        except Exception:
            body = {"raw": str(response.content)[:500]}
    if not isinstance(body, dict):
        body = {"raw": body}
    return int(getattr(response, "status_code", 500)), body


def run_verified_agent_mutation(
    ctx: OpsContext,
    *,
    tool: str,
    path: str,
    payload: dict[str, Any],
    permission: str | None = None,
    verify: Callable[[OpsContext, dict[str, Any], dict[str, Any]], OpsResult],
    guard_entity: Any | None = None,
    idempotency_key: str | None = None,
    method: str = "POST",
) -> OpsResult:
    """
    Execute a legacy agent route in-process, then DB read-back verify.

    Never returns verified=True based on HTTP status alone.
    """
    err = require_restaurant(ctx)
    if err:
        return err
    if permission:
        err = require_permission(ctx, permission)
        if err:
            return err
    if guard_entity is not None:
        loc_err = guard_entity_location(ctx, guard_entity)
        if loc_err:
            return loc_err

    if idempotency_key:
        try:
            from miya.services.message_pipeline import claim_mutation_once

            if not claim_mutation_once(idempotency_key, ttl_seconds=120):
                return ok(
                    message="That operation was already applied (duplicate suppressed).",
                    verified=True,
                    code="duplicate_suppressed",
                    data={"operation": tool, "deduplicated": True},
                )
        except Exception:
            logger.exception("idempotency claim failed for %s", tool)

    status_code, body = dispatch_agent_post(ctx, path, payload, method=method)
    if status_code >= 400 or body.get("success") is False or body.get("error"):
        msg = str(body.get("message_for_user") or body.get("error") or "The action failed.")
        return fail(
            code=str(body.get("code") or body.get("error") or "mutation_failed")[:64],
            message=msg,
            data={"operation": tool, "status_code": status_code, "details": body},
        )

    try:
        return verify(ctx, body, payload)
    except Exception as exc:
        logger.exception("verify failed for %s", tool)
        return fail(
            code="verify_failed",
            message="I couldn't verify the change in the database.",
            data={"operation": tool, "error": str(exc)[:200]},
        )
