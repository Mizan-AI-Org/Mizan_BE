"""
Context Engine — server-side execution context for every Miya turn.

Never invent user_id / organization_id / establishment_id / permissions from the LLM.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone as dt_timezone
from typing import Any
from zoneinfo import ZoneInfo


@dataclass
class ExecutionContext:
    """Authoritative context for one Miya message turn."""

    user_id: str
    organization_id: str  # restaurant / tenant
    establishment_id: str = ""
    role: str = ""
    permissions: list[str] = field(default_factory=list)
    department: str = ""
    conversation_id: str = ""
    message_id: str = ""
    channel: str = "dashboard"
    locale: str = "en"
    current_time: str = ""
    timezone: str = "UTC"
    current_conversation_context: dict[str, Any] = field(default_factory=dict)
    # Internal handles (not serialized to LLM by default)
    user: Any = None
    restaurant: Any = None
    organization_name: str = ""
    establishment_name: str = ""
    available_establishments: list[dict[str, Any]] = field(default_factory=list)

    def to_public_dict(self) -> dict[str, Any]:
        """Safe fields for logging / session — no ORM objects."""
        return {
            "user_id": self.user_id,
            "organization_id": self.organization_id,
            "organization_name": self.organization_name,
            "establishment_id": self.establishment_id or None,
            "establishment_name": self.establishment_name or None,
            "role": self.role,
            "permissions": list(self.permissions),
            "department": self.department or None,
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "channel": self.channel,
            "locale": self.locale,
            "current_time": self.current_time,
            "timezone": self.timezone,
            "available_establishments": list(self.available_establishments),
            "current_conversation_context": dict(self.current_conversation_context or {}),
        }

    def to_ops_context(self):
        """Bridge to existing canonical OpsContext."""
        from miya.services.ops.context import OpsContext

        return OpsContext(
            user=self.user,
            restaurant=self.restaurant,
            restaurant_id=self.organization_id,
            user_id=self.user_id,
            role=(self.role or "").upper(),
            channel=self.channel,
            language=self.locale,
            location_id=self.establishment_id or None,
            location_name=self.establishment_name or None,
            available_locations=list(self.available_establishments),
        )

    def attach_to_session(self, session_context: dict[str, Any] | None) -> dict[str, Any]:
        """Stamp pipeline + intelligence fields onto the live session dict."""
        out = dict(session_context or {})
        out["_execution_context"] = self.to_public_dict()
        out["_pipeline_message_id"] = self.message_id
        out["_pipeline_conversation_id"] = self.conversation_id
        if self.organization_id:
            out.setdefault("restaurant_id", self.organization_id)
        if self.establishment_id:
            out["location_id"] = self.establishment_id
            if self.establishment_name:
                out["location_name"] = self.establishment_name
        out.setdefault("channel", self.channel)
        out.setdefault("language", self.locale)
        out.setdefault("user_id", self.user_id)
        out.setdefault("role", self.role)
        return out


def _resolve_permissions(user, restaurant) -> list[str]:
    try:
        from accounts.rbac_enforce import allowed_tools_for_user

        return sorted(allowed_tools_for_user(user, restaurant=restaurant))
    except Exception:
        return []


def _department_for_user(user) -> str:
    for attr in ("department", "team", "job_title", "position"):
        val = getattr(user, attr, None)
        if val:
            return str(val).strip()
    return ""


def build_execution_context(
    *,
    user,
    channel: str = "dashboard",
    session_hint: dict[str, Any] | None = None,
    preferred_restaurant_id: str | None = None,
    inbound_message_id: str | None = None,
    conversation_id: str | None = None,
    conversation_context: dict[str, Any] | None = None,
) -> ExecutionContext:
    """
    Build ExecutionContext entirely server-side from auth + session hints.
    """
    from miya.services.context import build_session_context
    from miya.services.message_pipeline import begin_turn, new_conversation_id

    hint = dict(session_hint or {})
    session = build_session_context(
        user,
        channel=channel,
        preferred_restaurant_id=preferred_restaurant_id,
        session_hint=hint,
    )
    restaurant = None
    rid = session.get("restaurant_id")
    if rid:
        try:
            from accounts.models import Restaurant

            restaurant = Restaurant.objects.filter(id=rid).first()
        except Exception:
            restaurant = getattr(user, "restaurant", None)
    if restaurant is None:
        restaurant = getattr(user, "restaurant", None)

    turn = begin_turn(
        user=user,
        channel=channel,
        session_context=session,
        inbound_message_id=inbound_message_id or hint.get("inbound_wamid"),
    )
    conv_id = (conversation_id or "").strip() or turn.conversation_id
    if not conv_id:
        conv_id = new_conversation_id(channel, session.get("thread_id") or str(getattr(user, "id", "")))

    tz_name = str(session.get("timezone") or getattr(restaurant, "timezone", None) or "UTC")
    try:
        tz = ZoneInfo(tz_name)
        now = datetime.now(tz)
    except Exception:
        now = datetime.now(dt_timezone.utc)
        tz_name = "UTC"

    return ExecutionContext(
        user_id=str(session.get("user_id") or getattr(user, "id", "") or ""),
        organization_id=str(session.get("restaurant_id") or "") ,
        organization_name=str(session.get("restaurant_name") or ""),
        establishment_id=str(session.get("location_id") or ""),
        establishment_name=str(session.get("location_name") or ""),
        available_establishments=list(session.get("available_locations") or []),
        role=str(session.get("role") or getattr(user, "role", "") or ""),
        permissions=_resolve_permissions(user, restaurant),
        department=_department_for_user(user),
        conversation_id=conv_id,
        message_id=turn.message_id,
        channel=(channel or "dashboard").strip().lower(),
        locale=str(session.get("language") or "en"),
        current_time=now.isoformat(),
        timezone=tz_name,
        current_conversation_context=dict(conversation_context or {}),
        user=user,
        restaurant=restaurant,
    )


def execution_context_from_session(
    *,
    user,
    session_context: dict[str, Any] | None,
    restaurant=None,
) -> ExecutionContext | None:
    """Rehydrate a lightweight ExecutionContext from an existing session dict."""
    ctx = session_context or {}
    cached = ctx.get("_execution_context")
    rid = str(ctx.get("restaurant_id") or (getattr(restaurant, "id", None) or "")).strip()
    if not user or not rid:
        return None
    if restaurant is None:
        restaurant = getattr(user, "restaurant", None)
        try:
            from accounts.models import Restaurant

            restaurant = Restaurant.objects.filter(id=rid).first() or restaurant
        except Exception:
            pass

    public = cached if isinstance(cached, dict) else {}
    return ExecutionContext(
        user_id=str(public.get("user_id") or ctx.get("user_id") or getattr(user, "id", "") or ""),
        organization_id=rid,
        organization_name=str(public.get("organization_name") or ctx.get("restaurant_name") or ""),
        establishment_id=str(public.get("establishment_id") or ctx.get("location_id") or ""),
        establishment_name=str(public.get("establishment_name") or ctx.get("location_name") or ""),
        available_establishments=list(
            public.get("available_establishments") or ctx.get("available_locations") or []
        ),
        role=str(public.get("role") or ctx.get("role") or getattr(user, "role", "") or ""),
        permissions=list(public.get("permissions") or _resolve_permissions(user, restaurant)),
        department=str(public.get("department") or _department_for_user(user) or ""),
        conversation_id=str(
            public.get("conversation_id")
            or ctx.get("_pipeline_conversation_id")
            or ctx.get("thread_id")
            or ""
        ),
        message_id=str(
            public.get("message_id") or ctx.get("_pipeline_message_id") or ""
        ),
        channel=str(public.get("channel") or ctx.get("channel") or "dashboard").lower(),
        locale=str(public.get("locale") or ctx.get("language") or "en"),
        current_time=str(public.get("current_time") or ""),
        timezone=str(public.get("timezone") or ctx.get("timezone") or "UTC"),
        current_conversation_context=dict(
            public.get("current_conversation_context") or {}
        ),
        user=user,
        restaurant=restaurant,
    )
