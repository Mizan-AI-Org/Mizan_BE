#!/usr/bin/env python
"""
Live E2E audit: Miya tools, RBAC (manager vs staff), config, WhatsApp readiness.
Uses existing DB — no test database required.

  cd mizan-backend && DJANGO_SETTINGS_MODULE=mizan.settings .venv/bin/python scripts/e2e_miya_audit.py
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mizan.settings")

import django

django.setup()

from django.conf import settings
from django.utils import timezone

from accounts.models import CustomUser, Restaurant
from accounts.rbac_enforce import allowed_tools_for_user, user_can_use_miya
from core.whatsapp_config import (
    get_miya_whatsapp_enabled,
    get_whatsapp_access_token,
    get_whatsapp_phone_number_id,
)
from miya.services.tool_dispatch import dispatch_agent_request
from miya.services.tools import TOOL_SCHEMAS, _ROUTE_MAP, tools_for_user


@dataclass
class Check:
    area: str
    name: str
    status: str  # pass | fail | skip | partial
    detail: str = ""


RESULTS: list[Check] = []


def record(area: str, name: str, status: str, detail: str = "") -> None:
    RESULTS.append(Check(area, name, status, detail))


def config_audit() -> None:
    area = "Config"
    openai = bool(getattr(settings, "OPENAI_API_KEY", "") or os.environ.get("OPENAI_API_KEY"))
    record(area, "OPENAI_API_KEY", "pass" if openai else "fail", "Required for Miya chat")

    fish = bool(getattr(settings, "FISH_AUDIO_API_KEY", "") or os.environ.get("FISH_AUDIO_API_KEY"))
    record(area, "FISH_AUDIO_API_KEY", "pass" if fish else "partial", "Voice TTS; chat works without it")

    mastra_key = bool(getattr(settings, "MIYA_MASTRA_API_KEY", ""))
    record(area, "MIYA_MASTRA_API_KEY", "pass" if mastra_key else "fail", "Mastra bridge auth")

    wa_token = bool(get_whatsapp_access_token())
    wa_phone = bool(get_whatsapp_phone_number_id())
    wa_enabled = get_miya_whatsapp_enabled()
    if wa_token and wa_phone:
        record(area, "WhatsApp Meta credentials", "pass", f"MIYA_WHATSAPP_ENABLED={wa_enabled}")
    else:
        record(area, "WhatsApp Meta credentials", "fail", "Missing token or phone_number_id")

    gcal = bool(getattr(settings, "GOOGLE_OAUTH_CLIENT_ID", ""))
    record(area, "Google Calendar OAuth", "pass" if gcal else "partial", "Meetings/reminders need OAuth per user")


def pick_users(restaurant: Restaurant) -> tuple[CustomUser | None, CustomUser | None]:
    mgr = (
        CustomUser.objects.filter(restaurant=restaurant, role__in=["MANAGER", "OWNER", "ADMIN"], is_active=True)
        .first()
    )
    staff = (
        CustomUser.objects.filter(
            restaurant=restaurant,
            role__in=["WAITER", "CHEF", "CASHIER", "SUPERVISOR"],
            is_active=True,
        )
        .exclude(id=mgr.id if mgr else None)
        .first()
    )
    if not staff:
        staff = CustomUser.objects.filter(restaurant=restaurant, is_active=True).exclude(
            role__in=["SUPER_ADMIN", "MANAGER", "OWNER", "ADMIN"]
        ).first()
    return mgr, staff


def rbac_audit(restaurant: Restaurant) -> tuple[CustomUser | None, CustomUser | None]:
    area = "RBAC"
    mgr, staff = pick_users(restaurant)

    if mgr:
        can = user_can_use_miya(mgr)
        tools = allowed_tools_for_user(mgr, restaurant)
        record(area, f"Manager ({mgr.role}) Miya access", "pass" if can else "fail", f"{len(tools)} tools")
        for t in ("list_shifts", "create_dashboard_task", "ops_search", "staff_lookup"):
            record(
                area,
                f"Manager tool: {t}",
                "pass" if t in tools else "fail",
            )
    else:
        record(area, "Manager user", "skip", "No manager in tenant")

    if staff:
        can = user_can_use_miya(staff)
        tools = allowed_tools_for_user(staff, restaurant)
        record(area, f"Staff ({staff.role}) Miya access", "pass" if can else "fail", f"{len(tools)} tools")
        for t, expect in (
            ("my_shifts", True),
            ("staff_clock_in", True),
            ("staff_request", True),
            ("create_dashboard_task", False),
            ("list_shifts", False),
            ("ops_search", False),
        ):
            has = t in tools
            ok = has == expect
            record(
                area,
                f"Staff tool {t} (expect {'yes' if expect else 'no'})",
                "pass" if ok else "partial" if has and not expect else "fail",
                "has tool" if has else "blocked",
            )
    else:
        record(area, "Staff user", "skip", "No frontline staff in tenant")

    return mgr, staff


def probe_tool(name: str, method: str, path: str, payload: dict, headers: dict) -> tuple[int, dict]:
    code, body = dispatch_agent_request(method, path, json_payload=payload, headers=headers)
    if isinstance(body, dict):
        return code, body
    return code, {"raw": str(body)[:200]}


def tool_probes(restaurant: Restaurant, mgr: CustomUser | None, staff: CustomUser | None) -> None:
    area = "Tools (in-process)"
    key = getattr(settings, "MIYA_MASTRA_API_KEY", "")
    if not key:
        record(area, "All tools", "skip", "MIYA_MASTRA_API_KEY missing")
        return

    rid = str(restaurant.id)
    today = timezone.localdate().isoformat()
    base_headers = {"Authorization": f"Bearer {key}", "X-Restaurant-Id": rid}

    probes: list[tuple[str, str, str, dict, bool]] = [
        ("list_shifts", "GET", _ROUTE_MAP["list_shifts"][1], {"restaurant_id": rid, "date_from": today, "date_to": today}, True),
        ("get_business_context", "GET", _ROUTE_MAP["get_business_context"][1], {"restaurant_id": rid}, True),
        ("ops_search", "GET", _ROUTE_MAP["ops_search"][1], {"q": "staff", "restaurant_id": rid}, True),
        ("list_dashboard_tasks", "POST", _ROUTE_MAP["list_dashboard_tasks"][1], {"restaurant_id": rid, "limit": 5}, True),
        ("list_automations", "POST", _ROUTE_MAP["list_automations"][1], {"restaurant_id": rid}, True),
        ("list_staff_requests", "GET", _ROUTE_MAP["list_staff_requests"][1], {"restaurant_id": rid, "status": "PENDING"}, True),
        ("list_inventory", "GET", _ROUTE_MAP["list_inventory"][1], {"restaurant_id": rid, "limit": 3}, True),
        ("sales_summary", "GET", _ROUTE_MAP["sales_summary"][1], {"restaurant_id": rid}, True),
        ("proactive_insights", "GET", _ROUTE_MAP["proactive_insights"][1], {"restaurant_id": rid}, True),
        ("platform_knowledge", "POST", _ROUTE_MAP["platform_knowledge"][1], {"topic": "shifts"}, False),
    ]

    if staff and staff.phone:
        probes.append(
            (
                "my_shifts",
                "GET",
                _ROUTE_MAP["my_shifts"][1],
                {"phone": staff.phone, "when": "today"},
                True,
            )
        )

    for name, method, path, payload, expect_ok in probes:
        code, body = probe_tool(name, method, path, payload, base_headers)
        ok = code == 200 and (body.get("success") is not False) and "error" not in body or body.get("shifts") is not None
        if name == "list_shifts":
            ok = code == 200 and body.get("success") is True
        if name == "ops_search":
            ok = code == 200
        if name == "platform_knowledge":
            ok = code in (200, 201)

        err = body.get("error") or body.get("detail") or ""
        if code >= 500:
            record(area, name, "fail", f"HTTP {code}: {err or body}")
        elif ok or (expect_ok and code == 200):
            record(area, name, "pass", f"HTTP {code}")
        elif code in (400, 401, 403, 404):
            record(area, name, "partial", f"HTTP {code}: {err or 'needs data/context'}")
        else:
            record(area, name, "fail", f"HTTP {code}: {err or str(body)[:120]}")


def miya_chat_probe(mgr: CustomUser | None) -> None:
    area = "Miya chat"
    if not mgr:
        record(area, "run_miya_chat", "skip", "No manager user")
        return
    from rest_framework_simplejwt.tokens import RefreshToken

    token = str(RefreshToken.for_user(mgr).access_token)
    try:
        from miya.services.agent import run_miya_chat

        r = run_miya_chat(
            user=mgr,
            access_token=token,
            user_message="who is on duty today?",
            history=[],
            channel="dashboard",
            preferred_restaurant_id=str(mgr.restaurant_id) if mgr.restaurant_id else None,
        )
        reply = (r.get("reply") or "").strip()
        tools_used = r.get("tools_used") or []
        if reply and "step limit" not in reply.lower() and "technical issue" not in reply.lower():
            record(area, "who is on duty today?", "pass", f"tools={tools_used}; reply_len={len(reply)}")
        elif reply:
            record(area, "who is on duty today?", "partial", reply[:160])
        else:
            record(area, "who is on duty today?", "fail", "Empty reply")
    except Exception as exc:
        record(area, "run_miya_chat", "fail", str(exc)[:200])


def whatsapp_readiness(restaurant: Restaurant, mgr: CustomUser | None, staff: CustomUser | None) -> None:
    area = "WhatsApp"
    if not get_miya_whatsapp_enabled():
        record(area, "Miya WhatsApp handler", "partial", "MIYA_WHATSAPP_ENABLED=false")
    else:
        record(area, "Miya WhatsApp handler", "pass", "Enabled in config")

    for label, user in (("Manager", mgr), ("Staff", staff)):
        if not user:
            continue
        phone = (user.phone or "").strip()
        if phone:
            record(area, f"{label} phone linked", "pass", phone[-4:].rjust(len(phone), "*"))
        else:
            record(area, f"{label} phone linked", "fail", "No phone — WA won't resolve user")

    from notifications.models import WhatsAppSession

    sessions = WhatsAppSession.objects.filter(user__restaurant=restaurant).count()
    record(area, "WhatsApp sessions for tenant", "pass" if sessions else "partial", f"{sessions} session(s)")


def registry_audit() -> None:
    area = "Registry"
    schema_names = {(s.get("function") or {}).get("name") for s in TOOL_SCHEMAS}
    routes = set(_ROUTE_MAP.keys())
    if schema_names == routes:
        record(area, "Tool schemas ↔ routes", "pass", f"{len(routes)} tools")
    else:
        missing = routes - schema_names
        extra = schema_names - routes
        record(area, "Tool schemas ↔ routes", "fail", f"missing={missing} extra={extra}")


def main() -> int:
    print("=" * 60)
    print("Mizan AI / Miya E2E Audit")
    print(f"Date: {timezone.now().isoformat()}")
    print("=" * 60)

    config_audit()
    registry_audit()

    restaurant = Restaurant.objects.first()
    if not restaurant:
        record("Tenant", "Restaurant", "fail", "No restaurant in DB")
    else:
        record("Tenant", "Workspace", "pass", f"{restaurant.name} ({restaurant.id})")
        mgr, staff = rbac_audit(restaurant)
        tool_probes(restaurant, mgr, staff)
        miya_chat_probe(mgr)
        whatsapp_readiness(restaurant, mgr, staff)

    # Summary
    by_status: dict[str, int] = {}
    for r in RESULTS:
        by_status[r.status] = by_status.get(r.status, 0) + 1

    print(f"\nSummary: {by_status}\n")
    current_area = ""
    for r in RESULTS:
        if r.area != current_area:
            current_area = r.area
            print(f"\n## {current_area}")
        icon = {"pass": "✅", "fail": "🔴", "partial": "🟡", "skip": "⏭"}.get(r.status, "?")
        detail = f" — {r.detail}" if r.detail else ""
        print(f"  {icon} {r.name}{detail}")

    fails = [r for r in RESULTS if r.status == "fail"]
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
