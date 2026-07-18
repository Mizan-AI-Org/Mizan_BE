#!/usr/bin/env python3
"""Live E2E probe against local Mizan API + frontend.

Runs authenticated platform-admin and tenant-billing flows, records pass/fail,
and prints a markdown report. Does not modify permanent passwords except a
temporary reset on a designated tenant user for checkout probes.
"""
from __future__ import annotations

import json
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests

BASE = "http://127.0.0.1:8000/api"
FE = "http://127.0.0.1:8080"
OPS_EMAIL = "ops@heymizan.ai"
OPS_PASSWORD = "MizanOps1!"
TENANT_EMAIL = "driss@test.com"
TENANT_PASSWORD = "E2eTenantTest1!"


@dataclass
class Finding:
    severity: str  # critical | high | medium | low | info | pass
    area: str
    title: str
    detail: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def add(self, severity: str, area: str, title: str, detail: str = "") -> None:
        self.findings.append(Finding(severity, area, title, detail))

    def ok(self, area: str, title: str, detail: str = "") -> None:
        self.add("pass", area, title, detail)

    def fail(self, severity: str, area: str, title: str, detail: str = "") -> None:
        self.add(severity, area, title, detail)


def login(email: str, password: str, *, ops: bool = False) -> str | None:
    path = "/platform/auth/login/" if ops else "/auth/login/"
    r = requests.post(
        f"{BASE}{path}",
        json={"email": email, "password": password},
        timeout=20,
    )
    if r.status_code != 200:
        if ops:
            return None
        # Tenant fallback: JWT pair endpoint (also blocks platform ops).
        r2 = requests.post(
            f"{BASE}/token/",
            json={"email": email, "password": password},
            timeout=20,
        )
        if r2.status_code == 200:
            data = r2.json()
            return data.get("access") or data.get("access_token")
        return None
    data = r.json()
    return data.get("access") or data.get("tokens", {}).get("access") or data.get("access_token")


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def ensure_tenant_password() -> None:
    """Set a known temp password for the tenant user via Django ORM."""
    import os
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mizan.settings")
    sys.path.insert(0, "/Users/macbookpro/code/Mizan_AI/mizan-backend")
    django.setup()
    from accounts.models import CustomUser

    u = CustomUser.objects.filter(email__iexact=TENANT_EMAIL).first()
    if not u:
        raise RuntimeError(f"Tenant user {TENANT_EMAIL} not found")
    u.set_password(TENANT_PASSWORD)
    u.save(update_fields=["password"])


def get_json(r: requests.Response) -> Any:
    try:
        return r.json()
    except Exception:
        return {"_raw": r.text[:500]}


def run() -> Report:
    report = Report()
    session = requests.Session()

    # --- Frontend up ---
    try:
        fe = session.get(f"{FE}/admin", timeout=10)
        if fe.status_code == 200:
            report.ok("frontend", "Platform Admin shell loads", f"GET {FE}/admin → {fe.status_code}")
        else:
            report.fail("high", "frontend", "Platform Admin shell unexpected status", str(fe.status_code))
    except Exception as exc:
        report.fail("critical", "frontend", "Frontend not reachable on :8080", str(exc))

    # --- Ops login ---
    ops_token = login(OPS_EMAIL, OPS_PASSWORD, ops=True)
    if not ops_token:
        report.fail("critical", "auth", "Ops login failed", f"{OPS_EMAIL}")
        return report
    report.ok("auth", "Ops operator can sign in", OPS_EMAIL)
    oh = auth_headers(ops_token)

    # --- Platform me ---
    r = session.get(f"{BASE}/platform/me/", headers=oh, timeout=15)
    me = get_json(r)
    if r.status_code == 200 and me.get("is_platform_operator"):
        report.ok("platform", "/platform/me returns operator", json.dumps(me)[:200])
    else:
        report.fail("critical", "platform", "/platform/me failed or not operator", f"{r.status_code} {me}")

    # --- Overview ---
    r = session.get(f"{BASE}/platform/overview/", headers=oh, timeout=20)
    overview = get_json(r)
    if r.status_code == 200 and "restaurants" in overview:
        report.ok("platform", "Overview metrics load", f"tenants={overview.get('restaurants')} users={overview.get('users_active')}")
        if "stripe_configured" in (overview.get("health") or {}):
            # still present in API — OK if unused by UI
            report.add("info", "platform", "Overview still exposes stripe_configured in health", "UI may ignore it")
    else:
        report.fail("high", "platform", "Overview failed", f"{r.status_code} {overview}")

    # --- Tenants list + pagination ---
    r = session.get(f"{BASE}/platform/tenants/?page=1&page_size=5", headers=oh, timeout=20)
    tenants = get_json(r)
    if r.status_code == 200 and isinstance(tenants.get("results"), list):
        report.ok(
            "platform",
            "Tenants paginated list works",
            f"count={tenants.get('count')} page={tenants.get('page')} returned={len(tenants['results'])}",
        )
        if tenants.get("count", 0) > 5 and len(tenants["results"]) > 5:
            report.fail("medium", "platform", "Tenants page_size ignored", f"got {len(tenants['results'])} rows")
        elif tenants.get("count", 0) > 5 and len(tenants["results"]) == 5:
            report.ok("platform", "Tenants page_size respected", "5 results")
    else:
        report.fail("high", "platform", "Tenants list failed", f"{r.status_code} {tenants}")
        tenants = {"results": []}

    tenant_id = None
    if tenants.get("results"):
        tenant_id = tenants["results"][0]["id"]

    # --- Tenant detail ---
    if tenant_id:
        r = session.get(f"{BASE}/platform/tenants/{tenant_id}/", headers=oh, timeout=20)
        detail = get_json(r)
        if r.status_code == 200 and detail.get("id"):
            report.ok("platform", "Tenant detail loads", detail.get("name", ""))
            staff = detail.get("staff") or []
            if isinstance(staff, list):
                report.ok("platform", "Tenant detail includes staff list", f"n={len(staff)}")
        else:
            report.fail("high", "platform", "Tenant detail failed", f"{r.status_code} {detail}")

    # --- Users: operators excluded ---
    r = session.get(f"{BASE}/platform/users/?page=1&page_size=50", headers=oh, timeout=20)
    users = get_json(r)
    if r.status_code == 200:
        results = users.get("results") or []
        ops_in_list = [u for u in results if u.get("is_platform_operator") or u.get("email") == OPS_EMAIL]
        if ops_in_list:
            report.fail(
                "high",
                "platform",
                "Platform operators still appear on Users list",
                ", ".join(u.get("email", "?") for u in ops_in_list),
            )
        else:
            report.ok("platform", "Users list excludes platform operators", f"count={users.get('count')}")
    else:
        report.fail("high", "platform", "Users list failed", f"{r.status_code} {users}")

    # --- Operators ---
    r = session.get(f"{BASE}/platform/operators/", headers=oh, timeout=15)
    ops = get_json(r)
    if r.status_code == 200 and any(u.get("email") == OPS_EMAIL for u in (ops.get("results") or [])):
        report.ok("platform", "Operators list includes ops account", OPS_EMAIL)
    elif r.status_code == 200:
        report.fail("medium", "platform", "Ops email missing from operators list", str(ops)[:300])
    else:
        report.fail("high", "platform", "Operators list failed", f"{r.status_code} {ops}")

    # --- Billing subscriptions pagination ---
    r = session.get(f"{BASE}/platform/billing/subscriptions/?page=1&page_size=5", headers=oh, timeout=20)
    subs = get_json(r)
    if r.status_code == 200 and "results" in subs:
        report.ok("platform", "Billing subscriptions list works", f"count={subs.get('count')}")
    else:
        report.fail("high", "platform", "Billing subscriptions failed", f"{r.status_code} {subs}")

    # --- Health: Stripe optional ---
    r = session.get(f"{BASE}/platform/health/", headers=oh, timeout=15)
    health = get_json(r)
    if r.status_code == 200:
        items = health.get("items") or []
        stripe_items = [i for i in items if i.get("id") == "stripe_configured"]
        required_fail = [i for i in items if i.get("required", True) and i.get("kind") != "optional" and not i.get("ok")]
        if health.get("ok") is True or (health.get("ok") is False and required_fail):
            if stripe_items and stripe_items[0].get("required") is False:
                report.ok("platform", "Stripe is optional on health", stripe_items[0].get("message", ""))
            elif not stripe_items:
                report.add("info", "platform", "No stripe item in health items", "")
            else:
                report.fail("medium", "platform", "Stripe still marked required on health", str(stripe_items[0]))
            if health.get("ok") and required_fail:
                report.fail("high", "platform", "Health ok=true but required checks failed", str(required_fail))
            elif health.get("ok"):
                report.ok("platform", "Overall health Healthy", health.get("summary", ""))
            else:
                report.add("info", "platform", "Overall health Degraded (required)", health.get("summary", ""))
        else:
            report.fail("medium", "platform", "Health payload unexpected", json.dumps(health)[:400])
        if not health.get("payments"):
            report.fail("low", "platform", "Health missing payments note block", "")
        else:
            report.ok("platform", "Health includes payments note", health["payments"].get("note", "")[:80])
    else:
        report.fail("high", "platform", "Health endpoint failed", f"{r.status_code} {health}")

    # --- Audit pagination ---
    r = session.get(f"{BASE}/platform/audit/?page=1&page_size=5", headers=oh, timeout=20)
    audit = get_json(r)
    if r.status_code == 200 and "results" in audit and "page" in audit:
        report.ok("platform", "Audit pagination works", f"count={audit.get('count')} page={audit.get('page')}")
    elif r.status_code == 200:
        report.fail("medium", "platform", "Audit missing page field", str(audit.keys()))
    else:
        report.fail("high", "platform", "Audit failed", f"{r.status_code} {audit}")

    # --- Impersonate ---
    if tenant_id:
        r = session.post(
            f"{BASE}/platform/impersonate/",
            headers=oh,
            json={"restaurant_id": tenant_id},
            timeout=20,
        )
        imp = get_json(r)
        if r.status_code == 200 and imp.get("access"):
            report.ok("platform", "Impersonation issues tenant JWT", imp.get("restaurant", {}).get("name", ""))
            tenant_jwt = imp["access"]
            # Impersonated user must NOT access platform APIs
            r2 = session.get(
                f"{BASE}/platform/tenants/{tenant_id}/",
                headers=auth_headers(tenant_jwt),
                timeout=15,
            )
            if r2.status_code in (401, 403):
                report.ok("platform", "Impersonated JWT blocked from platform APIs", str(r2.status_code))
            else:
                report.fail(
                    "critical",
                    "platform",
                    "Impersonated JWT can still call platform APIs",
                    f"{r2.status_code} {get_json(r2)}",
                )
            # Can call tenant me
            r3 = session.get(f"{BASE}/auth/me/", headers=auth_headers(tenant_jwt), timeout=15)
            if r3.status_code == 200:
                report.ok("platform", "Impersonated JWT works for /auth/me", get_json(r3).get("email", ""))
            else:
                report.fail("high", "platform", "Impersonated /auth/me failed", f"{r3.status_code}")
        else:
            report.fail("high", "platform", "Impersonation failed", f"{r.status_code} {imp}")

    # --- Tenant billing E2E ---
    try:
        ensure_tenant_password()
        report.ok("billing", "Set temp tenant password for E2E", TENANT_EMAIL)
    except Exception as exc:
        report.fail("critical", "billing", "Could not set tenant password", str(exc))
        return report

    tenant_token = login(TENANT_EMAIL, TENANT_PASSWORD)
    if not tenant_token:
        report.fail("critical", "billing", "Tenant login failed after password reset", TENANT_EMAIL)
        return report
    report.ok("billing", "Tenant login works", TENANT_EMAIL)
    th = auth_headers(tenant_token)

    r = session.get(f"{BASE}/billing/plans/", headers=th, timeout=15)
    plans = get_json(r)
    if r.status_code == 200 and isinstance(plans, list) and plans:
        report.ok("billing", "Plans list returns published plans", f"n={len(plans)}")
        # AllowAny also without auth
        r_pub = session.get(f"{BASE}/billing/plans/", timeout=15)
        if r_pub.status_code == 200:
            report.ok("billing", "Plans are publicly readable", "")
    else:
        # might be paginated
        if isinstance(plans, dict) and plans.get("results"):
            plans = plans["results"]
            report.ok("billing", "Plans list paginated", f"n={len(plans)}")
        else:
            report.fail("high", "billing", "Plans list empty/failed", f"{r.status_code} {plans}")
            plans = []

    r = session.get(f"{BASE}/billing/subscription/", headers=th, timeout=15)
    sub = get_json(r)
    if r.status_code == 200:
        report.ok(
            "billing",
            "Current subscription loads",
            f"status={sub.get('status')} tier={sub.get('tier')} has_provider={sub.get('has_provider_subscription')} provider={sub.get('payment_provider')}",
        )
        if "has_provider_subscription" not in sub:
            report.fail("high", "billing", "Subscription missing has_provider_subscription", str(sub.keys()))
        if "payment_provider" not in sub:
            report.fail("medium", "billing", "Subscription missing payment_provider", "")
        if "pending_plan" not in sub:
            report.fail("medium", "billing", "Subscription missing pending_plan field", "")
    else:
        report.fail("critical", "billing", "Current subscription failed", f"{r.status_code} {sub}")
        return report

    r = session.get(f"{BASE}/billing/subscription/entitlements/", headers=th, timeout=15)
    ent = get_json(r)
    if r.status_code == 200 and "features" in ent:
        report.ok("billing", "Entitlements endpoint works", f"tier={ent.get('tier')} features={len(ent.get('features') or [])}")
    else:
        report.fail("medium", "billing", "Entitlements failed", f"{r.status_code} {ent}")

    # Pick a Growth (or non-current) plan with a price id if any
    upgrade_plan = None
    for p in plans if isinstance(plans, list) else []:
        if p.get("tier") == "GROWTH" and (p.get("stripe_price_id_monthly") or p.get("stripe_price_id")):
            upgrade_plan = p
            break
    if not upgrade_plan:
        for p in plans if isinstance(plans, list) else []:
            if p.get("stripe_price_id_monthly") or p.get("stripe_price_id"):
                upgrade_plan = p
                break

    if not upgrade_plan:
        # Still test queued path with first plan's fake? Backend requires known price_id
        report.add(
            "info",
            "billing",
            "No plan has Stripe price IDs — checkout will rely on queued/contact path",
            "Seed STRIPE_PRICE_* or expect action=queued only if price_id known",
        )
        # Try checkout with missing price should 400
        r = session.post(
            f"{BASE}/billing/subscription/checkout/",
            headers=th,
            json={
                "price_id": "price_does_not_exist",
                "success_url": f"{FE}/dashboard/settings?tab=billing&billing=success",
                "cancel_url": f"{FE}/dashboard/settings?tab=billing&billing=cancelled",
                "billing_interval": "month",
            },
            timeout=20,
        )
        if r.status_code == 400:
            report.ok("billing", "Checkout rejects unknown price_id", get_json(r).get("error", ""))
        else:
            report.fail("medium", "billing", "Checkout unknown price_id unexpected", f"{r.status_code} {get_json(r)}")
    else:
        price_id = upgrade_plan.get("stripe_price_id_monthly") or upgrade_plan.get("stripe_price_id")
        r = session.post(
            f"{BASE}/billing/subscription/checkout/",
            headers=th,
            json={
                "price_id": price_id,
                "success_url": f"{FE}/dashboard/settings?tab=billing&billing=success",
                "cancel_url": f"{FE}/dashboard/settings?tab=billing&billing=cancelled",
                "billing_interval": "month",
            },
            timeout=30,
        )
        body = get_json(r)
        if r.status_code in (200, 202) and body.get("action") in {"redirect", "updated", "queued"}:
            report.ok(
                "billing",
                f"Upgrade start returned action={body.get('action')}",
                f"provider={body.get('provider')} msg={body.get('message', '')[:120]} url={'set' if body.get('url') else 'none'}",
            )
            if body.get("action") == "redirect" and not body.get("url"):
                report.fail("high", "billing", "action=redirect but url missing", str(body))
            if body.get("action") == "queued":
                r2 = session.get(f"{BASE}/billing/subscription/", headers=th, timeout=15)
                sub2 = get_json(r2)
                if sub2.get("pending_plan"):
                    report.ok("billing", "Queued upgrade stored pending_plan", str(sub2.get("pending_plan", {}).get("name")))
                else:
                    report.fail("high", "billing", "Queued upgrade did not set pending_plan", str(sub2)[:300])
        else:
            report.fail("high", "billing", "Upgrade checkout failed", f"{r.status_code} {body}")

    # Portal without provider sub should still try / error gracefully
    r = session.post(
        f"{BASE}/billing/subscription/portal/",
        headers=th,
        json={"return_url": f"{FE}/dashboard/settings?tab=billing"},
        timeout=20,
    )
    portal = get_json(r)
    if r.status_code == 200 and portal.get("url"):
        report.ok("billing", "Portal session created", "url returned")
    elif r.status_code == 503:
        report.add("info", "billing", "Portal returns 503 when Stripe not configured", portal.get("error", ""))
    else:
        # may create customer then fail portal config
        report.add("info", "billing", "Portal response", f"{r.status_code} {portal}")

    # Tenant must not hit platform
    r = session.get(f"{BASE}/platform/me/", headers=th, timeout=10)
    if r.status_code in (401, 403):
        report.ok("security", "Tenant JWT denied on /platform/me", str(r.status_code))
    else:
        report.fail("critical", "security", "Tenant can access /platform/me", f"{r.status_code} {get_json(r)}")

    return report


def render(report: Report) -> str:
    counts: dict[str, int] = {}
    for f in report.findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1

    lines = [
        f"# E2E Test Report — Mizan Platform Admin + Tenant Billing",
        "",
        f"**Ran at:** {report.started_at}",
        f"**Targets:** API `{BASE}` · FE `{FE}`",
        "",
        "## Summary",
        "",
        f"| Severity | Count |",
        f"|---|---|",
    ]
    for sev in ("critical", "high", "medium", "low", "info", "pass"):
        if sev in counts:
            lines.append(f"| {sev} | {counts[sev]} |")
    lines += ["", "## Findings", ""]

    for f in report.findings:
        if f.severity == "pass":
            lines.append(f"- ✅ **PASS** · `{f.area}` — {f.title}" + (f" — {f.detail}" if f.detail else ""))
        elif f.severity == "info":
            lines.append(f"- ℹ️ **INFO** · `{f.area}` — {f.title}" + (f" — {f.detail}" if f.detail else ""))
        else:
            icon = {"critical": "🟥", "high": "🟠", "medium": "🟡", "low": "🔵"}.get(f.severity, "•")
            lines.append(f"- {icon} **{f.severity.upper()}** · `{f.area}` — **{f.title}**")
            if f.detail:
                lines.append(f"  - {f.detail}")

    bugs = [f for f in report.findings if f.severity in {"critical", "high", "medium", "low"}]
    lines += ["", "## Bugs to fix (priority order)", ""]
    if not bugs:
        lines.append("No functional bugs detected by this probe.")
    else:
        for i, f in enumerate(bugs, 1):
            lines.append(f"{i}. **[{f.severity}]** {f.title} (`{f.area}`)")
            if f.detail:
                lines.append(f"   - {f.detail}")

    lines += [
        "",
        "## Coverage notes",
        "",
        "- API-level E2E against live local servers (not TestSprite; MCP unavailable).",
        "- Browser UI interactions (dark mode visuals, pagination controls, clicks) were not automated in this pass.",
        "- Full Stripe Checkout payment was not completed (no live Stripe keys / card flow).",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    try:
        rep = run()
    except Exception:
        traceback.print_exc()
        sys.exit(2)
    text = render(rep)
    out = "/Users/macbookpro/code/Mizan_AI/e2e-report.md"
    with open(out, "w") as f:
        f.write(text)
    print(text)
    print(f"\nWrote {out}")
    bugs = [f for f in rep.findings if f.severity in {"critical", "high"}]
    sys.exit(1 if bugs else 0)
