"""Read-through cache policy for Mastra tool dispatch."""

from __future__ import annotations

from django.conf import settings

from .services.tools import _GET_METHOD_TOOLS

# Idempotent read tools (GET or read-only POST) safe to cache briefly.
_MASTRA_READ_CACHE_TOOLS = frozenset(
    {
        *_GET_METHOD_TOOLS,
        "staff_lookup",
        "list_invoices",
        "list_dashboard_tasks",
        "get_dashboard_task",
        "list_automations",
        "platform_knowledge",
    }
)


def mastra_read_cache_ttl(tool_name: str) -> int | None:
    """Return TTL seconds for cacheable tools, or None when writes must bypass cache."""
    if tool_name not in _MASTRA_READ_CACHE_TOOLS:
        return None
    if tool_name in ("get_business_context", "proactive_insights"):
        return int(getattr(settings, "MIYA_CACHE_TTL_CONTEXT", 120) or 120)
    return int(getattr(settings, "MIYA_CACHE_TTL_TOOL", 90) or 90)


def whatsapp_context_cache_ttl() -> int:
    return int(getattr(settings, "MIYA_CACHE_TTL_WHATSAPP_CTX", 300) or 300)
