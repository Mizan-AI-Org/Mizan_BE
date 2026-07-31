"""Bearer-token auth for Mizan agent HTTP endpoints (Miya / Mastra bridge)."""

from __future__ import annotations

from django.conf import settings


def mastra_bridge_api_key() -> str:
    """Shared secret Mastra Cloud uses when calling Django agent tool routes."""
    return (getattr(settings, "MIYA_MASTRA_API_KEY", None) or "").strip()


def configured_agent_bearer_tokens() -> tuple[str, ...]:
    key = mastra_bridge_api_key()
    return (key,) if key else ()


def primary_agent_bearer_token() -> str:
    return mastra_bridge_api_key()


def is_agent_bearer(token: str | None) -> bool:
    if not token:
        return False
    key = mastra_bridge_api_key()
    return bool(key) and token.strip() == key


def validate_agent_bearer(request) -> tuple[bool, str | None]:
    """
    Validate Authorization: Bearer <MIYA_MASTRA_API_KEY>.
    """
    key = mastra_bridge_api_key()
    if not key:
        return False, "Agent key not configured"

    auth = (
        getattr(request, "headers", {}).get("Authorization")
        or request.META.get("HTTP_AUTHORIZATION")
        or ""
    ).strip()
    if not auth.lower().startswith("bearer "):
        return False, "Unauthorized"

    token = auth[7:].strip()
    if token == key:
        return True, None
    return False, "Unauthorized"
