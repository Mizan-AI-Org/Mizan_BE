"""
Smoke test for the Intelligent Staff Requests Inbox.

Validates:
  1. Model-level fields (assignee FK, voice fields, new categories) exist
     and behave as expected.
  2. ``staff.request_routing.resolve_default_assignee_for_category``
     reads ``Restaurant.general_settings['category_owners']`` and maps
     StaffRequest.category values onto the right owner.
  3. Agent ingest endpoint (``POST /api/staff/agent/requests/ingest/``):
       - rejects missing description
       - accepts new MAINTENANCE / RESERVATIONS / INVENTORY categories
       - auto-assigns from category_owners
       - persists voice_audio_url + transcription
  4. Agent assign endpoint (``POST /api/staff/agent/requests/assign/``)
     changes the FK and writes an audit comment.
  5. Manager endpoints:
       - ``GET /api/staff/requests/?assigned_to_me=1`` filters correctly
       - ``POST /api/staff/requests/{id}/reassign/`` works and is
         distinct from escalate (status unchanged).
  6. ``GET /api/staff/requests/counts/`` returns ``assigned_to_me_open``.

Run:
    cd mizan-backend
    PYTHONPATH=.:venv/lib/python3.13/site-packages \
        DJANGO_SETTINGS_MODULE=mizan.settings \
        python3.13 scripts/smoke_staff_requests_inbox.py
"""
from __future__ import annotations

import os
import sys
import uuid

import django
from django.test.utils import override_settings

if not os.environ.get("DJANGO_SETTINGS_MODULE"):
    os.environ["DJANGO_SETTINGS_MODULE"] = "mizan.settings"
django.setup()

from django.utils import timezone  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from accounts.models import CustomUser, Restaurant  # noqa: E402
from staff.models import StaffRequest, StaffRequestComment  # noqa: E402
from staff.request_routing import (  # noqa: E402
    resolve_default_assignee_for_category,
    slugs_for_category,
    ALL_CATEGORY_OWNER_SLUGS,
)

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def ok(label: str) -> None:
    PASSED.append(label)
    print(f"  [OK]  {label}")


def fail(label: str, detail: str) -> None:
    FAILED.append((label, detail))
    print(f"  [FAIL] {label}: {detail}")


def assert_eq(label: str, actual, expected) -> None:
    if actual == expected:
        ok(label)
    else:
        fail(label, f"expected {expected!r}, got {actual!r}")


def assert_true(label: str, condition, detail: str = "") -> None:
    if condition:
        ok(label)
    else:
        fail(label, detail or "condition was falsy")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def create_restaurant(name: str) -> Restaurant:
    return Restaurant.objects.create(name=name, email=f"{uuid.uuid4().hex[:6]}@inbox.test")


def create_user(rest: Restaurant, role: str, first: str, last: str) -> CustomUser:
    return CustomUser.objects.create(
        email=f"{first.lower()}.{last.lower()}.{uuid.uuid4().hex[:4]}@inbox.test",
        first_name=first,
        last_name=last,
        role=role,
        restaurant=rest,
        is_active=True,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_model_fields() -> None:
    print("\n[1] Model enhancements")
    field_names = {f.name for f in StaffRequest._meta.get_fields()}
    for f in ("assignee", "voice_audio_url", "transcription", "transcription_language"):
        assert_true(f"field '{f}' exists", f in field_names)

    choices = {c[0] for c in StaffRequest._meta.get_field("category").choices or []}
    for cat in ("MAINTENANCE", "RESERVATIONS", "INVENTORY"):
        assert_true(f"category '{cat}' added", cat in choices)


def test_routing_helper() -> None:
    print("\n[2] staff.request_routing")
    rest = create_restaurant("Routing Test")
    maint = create_user(rest, "STAFF", "Maint", "Owner")
    res = create_user(rest, "STAFF", "Res", "Owner")

    # No settings → no owner
    got = resolve_default_assignee_for_category(rest, "MAINTENANCE")
    assert_eq("no category_owners → None", got, None)

    rest.general_settings = {
        "category_owners": {
            "request.maintenance": str(maint.id),
            "request.reservations": str(res.id),
        }
    }
    rest.save(update_fields=["general_settings"])

    got = resolve_default_assignee_for_category(rest, "MAINTENANCE")
    assert_eq("MAINTENANCE → maintenance owner", got.id if got else None, maint.id)

    got = resolve_default_assignee_for_category(rest, "RESERVATIONS")
    assert_eq("RESERVATIONS → reservations owner", got.id if got else None, res.id)

    got = resolve_default_assignee_for_category(rest, "INVENTORY")
    assert_eq("INVENTORY (unset) → None", got, None)

    # Fallback chain for MAINTENANCE → incident.equipment
    rest.general_settings = {"category_owners": {"incident.equipment": str(maint.id)}}
    rest.save(update_fields=["general_settings"])
    got = resolve_default_assignee_for_category(rest, "MAINTENANCE")
    assert_eq(
        "MAINTENANCE falls back to incident.equipment",
        got.id if got else None,
        maint.id,
    )

    # Slug list sanity
    assert_true(
        "ALL_CATEGORY_OWNER_SLUGS includes request.maintenance",
        "request.maintenance" in ALL_CATEGORY_OWNER_SLUGS,
    )
    assert_eq(
        "slugs_for_category('INVENTORY') is correct",
        slugs_for_category("INVENTORY"),
        ("request.inventory",),
    )


@override_settings(LUA_WEBHOOK_API_KEY="inbox-smoke-key", ALLOWED_HOSTS=["*"])
def test_agent_ingest() -> None:
    print("\n[3] Agent ingest endpoint")
    rest = create_restaurant("Ingest Test")
    owner = create_user(rest, "MANAGER", "Owner", "Primary")
    maint_owner = create_user(rest, "STAFF", "Maint", "Primary")

    rest.general_settings = {
        "category_owners": {"request.maintenance": str(maint_owner.id)}
    }
    rest.save(update_fields=["general_settings"])

    client = APIClient()
    headers = {
        "HTTP_AUTHORIZATION": "Bearer inbox-smoke-key",
        "HTTP_X_RESTAURANT_ID": str(rest.id),
    }

    # Missing description → 400
    resp = client.post(
        "/api/staff/agent/requests/ingest/",
        data={"restaurant_id": str(rest.id)},
        format="json",
        **headers,
    )
    assert_eq("missing description returns 400", resp.status_code, 400)

    # MAINTENANCE request with voice — should auto-assign to maint_owner.
    resp = client.post(
        "/api/staff/agent/requests/ingest/",
        data={
            "restaurant_id": str(rest.id),
            "subject": "Ice machine is leaking",
            "description": "The ice machine in the bar is leaking all over the floor",
            "category": "MAINTENANCE",
            "priority": "HIGH",
            "voice_audio_url": "https://example.test/audio.ogg",
            "transcription": "The ice machine in the bar is leaking all over the floor",
            "transcription_language": "en",
            "phone": "+212600000001",
        },
        format="json",
        **headers,
    )
    assert_eq("MAINTENANCE ingest returns 201", resp.status_code, 201)
    body = resp.json()
    assert_true("response contains assignee", bool(body.get("assignee")))
    assert_eq(
        "auto-assigned to maintenance owner",
        body.get("assignee", {}).get("id"),
        str(maint_owner.id),
    )
    assert_true("auto_assigned flag True", body.get("assignee", {}).get("auto_assigned") is True)
    assert_eq("category stored as MAINTENANCE", body.get("category"), "MAINTENANCE")

    req = StaffRequest.objects.get(id=body["id"])
    assert_eq("voice_audio_url persisted", req.voice_audio_url, "https://example.test/audio.ogg")
    assert_eq("transcription persisted", req.transcription, "The ice machine in the bar is leaking all over the floor")
    assert_eq("transcription_language persisted", req.transcription_language, "en")
    assert_eq("assignee FK set", req.assignee_id, maint_owner.id)

    # Category alias ("reservation" → RESERVATIONS, even without an owner).
    resp = client.post(
        "/api/staff/agent/requests/ingest/",
        data={
            "restaurant_id": str(rest.id),
            "subject": "Move 8pm booking to 9pm",
            "description": "Regular customer wants to push back tonight's table",
            "category": "reservation",  # alias, lowercase
        },
        format="json",
        **headers,
    )
    assert_eq("category alias ingest returns 201", resp.status_code, 201)
    alias_body = resp.json()
    assert_eq("alias normalised to RESERVATIONS", alias_body.get("category"), "RESERVATIONS")
    assert_eq("no owner configured → None", alias_body.get("assignee"), None)

    # Keep refs for later tests.
    return rest, owner, maint_owner, body["id"]


@override_settings(LUA_WEBHOOK_API_KEY="inbox-smoke-key", ALLOWED_HOSTS=["*"])
def test_agent_assign(rest: Restaurant, maint_owner: CustomUser, req_id: str) -> None:
    print("\n[4] Agent assign endpoint")
    new_owner = create_user(rest, "STAFF", "New", "Owner")

    client = APIClient()
    headers = {
        "HTTP_AUTHORIZATION": "Bearer inbox-smoke-key",
        "HTTP_X_RESTAURANT_ID": str(rest.id),
    }

    # Bad request_id
    resp = client.post(
        "/api/staff/agent/requests/assign/",
        data={"restaurant_id": str(rest.id), "request_id": str(uuid.uuid4()), "assignee_id": str(new_owner.id)},
        format="json",
        **headers,
    )
    assert_eq("unknown request_id returns 404", resp.status_code, 404)

    # Happy path
    resp = client.post(
        "/api/staff/agent/requests/assign/",
        data={"restaurant_id": str(rest.id), "request_id": req_id, "assignee_id": str(new_owner.id), "note": "Maint is out today"},
        format="json",
        **headers,
    )
    assert_eq("assign returns 200", resp.status_code, 200)

    req = StaffRequest.objects.get(id=req_id)
    assert_eq("assignee FK updated", req.assignee_id, new_owner.id)

    comment = StaffRequestComment.objects.filter(request_id=req_id, kind="system").order_by("-created_at").first()
    assert_true("audit comment created", comment is not None)
    assert_true("audit comment mentions new assignee", "Reassigned" in (comment.body if comment else ""))


@override_settings(LUA_WEBHOOK_API_KEY="inbox-smoke-key", ALLOWED_HOSTS=["*"])
def test_manager_endpoints(rest: Restaurant, owner: CustomUser, maint_owner: CustomUser) -> None:
    print("\n[5] Manager endpoints (JWT)")
    client = APIClient()
    client.force_authenticate(user=owner)

    # Create one request owned by the caller and one by someone else.
    mine = StaffRequest.objects.create(
        restaurant=rest,
        subject="Mine",
        description="For me",
        category="OPERATIONS",
        assignee=owner,
    )
    _ = StaffRequest.objects.create(
        restaurant=rest,
        subject="Theirs",
        description="For maint",
        category="MAINTENANCE",
        assignee=maint_owner,
    )

    resp = client.get("/api/staff/requests/?assigned_to_me=1")
    assert_eq("assigned_to_me list returns 200", resp.status_code, 200)
    data = resp.json()
    rows = data["results"] if isinstance(data, dict) and "results" in data else data
    ids = {str(r["id"]) for r in (rows or [])}
    assert_true("assigned_to_me includes mine", str(mine.id) in ids)
    assert_true(
        "assigned_to_me excludes others",
        all(str(r["id"]) != str(_.id) for r in (rows or [])),
    )

    # counts includes assigned_to_me_open
    resp = client.get("/api/staff/requests/counts/?assigned_to_me=1")
    assert_eq("counts endpoint returns 200", resp.status_code, 200)
    body = resp.json()
    assert_true(
        "counts.assigned_to_me_open present",
        "assigned_to_me_open" in body,
    )
    assert_true(
        "assigned_to_me_open >= 1",
        int(body.get("assigned_to_me_open", 0)) >= 1,
    )

    # Reassign manager action: lateral move, status unchanged
    resp = client.post(
        f"/api/staff/requests/{mine.id}/reassign/",
        data={"assignee_id": str(maint_owner.id), "note": "Handoff"},
        format="json",
    )
    assert_eq("reassign returns 200", resp.status_code, 200)
    mine.refresh_from_db()
    assert_eq("reassign kept status PENDING", mine.status, "PENDING")
    assert_eq("reassign updated assignee", mine.assignee_id, maint_owner.id)


def run() -> None:
    print("=" * 72)
    print("Intelligent Staff Requests Inbox — smoke test")
    print("=" * 72)

    timestamp_start = timezone.now()

    test_model_fields()
    test_routing_helper()
    rest, owner, maint_owner, req_id = test_agent_ingest()
    test_agent_assign(rest, maint_owner, req_id)
    test_manager_endpoints(rest, owner, maint_owner)

    duration = (timezone.now() - timestamp_start).total_seconds()
    print("\n" + "=" * 72)
    print(f"Result: {len(PASSED)} passed, {len(FAILED)} failed  ({duration:.1f}s)")
    if FAILED:
        print("\nFailures:")
        for label, detail in FAILED:
            print(f"  - {label}: {detail}")
        sys.exit(1)
    print("=" * 72)


if __name__ == "__main__":
    run()
