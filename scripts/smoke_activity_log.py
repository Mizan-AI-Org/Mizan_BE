"""Smoke test for the "Miya memory" feature.

Validates:
  1. AuditLog has the new ``target_user`` and ``metadata`` columns.
  2. AuditLoggingMiddleware enriches entries with actor + target + metadata.
  3. ``/api/agent/activity-log/`` returns events with all filters.
  4. The response shape matches what ``ActivityLogTool.ts`` expects.
"""

import os
import sys
import uuid

# Force settings + allow localhost BEFORE django.setup() so the test client
# doesn't blow up on DisallowedHost. Same pattern as prior smoke scripts.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mizan.settings')
os.environ.setdefault('DJANGO_ALLOWED_HOSTS', 'localhost,testserver')
os.environ.setdefault('LUA_WEBHOOK_API_KEY', 'smoketest-key')

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.test import override_settings  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from accounts.models import AuditLog, CustomUser, Restaurant  # noqa: E402


def ok(label):
    print(f"  ✓ {label}")


def fail(label, detail=""):
    print(f"  ✗ {label}")
    if detail:
        print(f"    {detail}")
    sys.exit(1)


def section(title):
    print(f"\n=== {title} ===")


def _get_or_create_restaurant():
    rest, _ = Restaurant.objects.get_or_create(
        name='Smoketest Bistro',
        defaults={'email': 'smoketest-bistro@example.com'},
    )
    return rest


def _get_or_create_user(email, restaurant, role='MANAGER'):
    user, created = CustomUser.objects.get_or_create(
        email=email,
        defaults={
            'first_name': email.split('@')[0].title(),
            'last_name': 'Smoketest',
            'restaurant': restaurant,
            'role': role,
            'is_active': True,
        },
    )
    if not created:
        # Make sure role/restaurant match what the test assumes.
        user.restaurant = restaurant
        user.role = role
        user.is_active = True
        user.save(update_fields=['restaurant', 'role', 'is_active'])
    return user


def test_model_fields():
    section("Model — new columns on AuditLog")
    field_names = {f.name for f in AuditLog._meta.get_fields()}
    for f in ('target_user', 'metadata'):
        if f in field_names:
            ok(f"AuditLog has '{f}'")
        else:
            fail(f"AuditLog missing '{f}'")


def test_middleware_enrichment(manager, assignee):
    """Exercise AuditLoggingMiddleware in isolation.

    We drive it directly rather than via the URL dispatcher because picking
    a real endpoint that this specific MANAGER has permission for is brittle
    — permissions evolve and other smoke tests would break first. The
    middleware is self-contained; unit-driving it gives us the strongest
    possible contract test with no cross-app coupling.
    """
    import json
    from core.middleware import AuditLoggingMiddleware
    from django.http import JsonResponse
    from django.test import RequestFactory

    section("Middleware — enriches entries with target_user + metadata")

    AuditLog.objects.filter(restaurant=manager.restaurant).delete()

    factory = RequestFactory(HTTP_HOST='localhost')
    middleware = AuditLoggingMiddleware(lambda r: JsonResponse({'ok': True}))

    payload = {
        'first_name': 'Testy',
        'last_name': 'McTest',
        'email': f'testy+{uuid.uuid4().hex[:6]}@example.com',
        'phone': '+10000000000',
        'role': 'STAFF',
        # This is the field the middleware should extract as target_user.
        'assigned_to': str(assignee.id),
    }
    request = factory.post(
        '/api/staff/invite/',
        data=json.dumps(payload),
        content_type='application/json',
    )
    request.user = manager

    middleware.process_request(request)
    middleware.process_response(request, JsonResponse({'ok': True}))
    ok("Middleware pipeline completed")

    entry = AuditLog.objects.filter(
        restaurant=manager.restaurant, user=manager
    ).order_by('-timestamp').first()
    if not entry:
        fail("Middleware did not create an AuditLog row")

    ok(f"AuditLog row written (entity_type={entry.entity_type!r}, action_type={entry.action_type!r})")

    if entry.target_user_id == assignee.id:
        ok("target_user was extracted from request body")
    else:
        fail(
            "target_user not populated from `assigned_to`",
            detail=f"expected={assignee.id}, got={entry.target_user_id}",
        )

    if not entry.metadata:
        fail("metadata is empty")
    required = {'method', 'path', 'status_code'}
    missing = required - set(entry.metadata.keys())
    if missing:
        fail(f"metadata missing keys: {sorted(missing)}")
    ok(f"metadata populated: method={entry.metadata.get('method')}, status={entry.metadata.get('status_code')}")

    if entry.description.startswith('POST '):
        fail("description did not get enriched", detail=entry.description)
    ok(f"description enriched: {entry.description!r}")

    # Negative case: no assignee field → target_user stays None, but the
    # row must still be written so "who logged in?" queries keep working.
    AuditLog.objects.filter(restaurant=manager.restaurant).delete()
    req2 = factory.post(
        '/api/staff/invite/',
        data=json.dumps({'email': 'noassignee@example.com'}),
        content_type='application/json',
    )
    req2.user = manager
    middleware.process_request(req2)
    middleware.process_response(req2, JsonResponse({'ok': True}))
    entry2 = AuditLog.objects.filter(restaurant=manager.restaurant).order_by('-timestamp').first()
    if entry2 is None:
        fail("Middleware skipped a valid mutating request")
    if entry2.target_user_id is not None:
        fail("target_user should be None when no assignee field is present")
    ok("target_user correctly None when body has no assignee field")


def _seed_entries(manager, assignee, other_assignee):
    """Directly seed a few varied rows so we can exercise filters."""
    rest = manager.restaurant
    rows = [
        # Actor=manager, Target=assignee, TASK.CREATE
        dict(
            action_type='CREATE', entity_type='TASK', entity_id=str(uuid.uuid4()),
            description=f"{manager.first_name} created a task (assigned to {assignee.first_name})",
            metadata={'method': 'POST', 'path': '/api/scheduling/tasks/', 'status_code': 201},
            target_user=assignee,
        ),
        # Actor=manager, Target=other, SHIFT.UPDATE
        dict(
            action_type='UPDATE', entity_type='SHIFT', entity_id=str(uuid.uuid4()),
            description=f"{manager.first_name} reassigned a shift to {other_assignee.first_name}",
            metadata={'method': 'PATCH', 'path': '/api/scheduling/shifts/x/', 'status_code': 200},
            target_user=other_assignee,
        ),
        # Actor=assignee, Target=None, AUTH.LOGIN
        dict(
            action_type='LOGIN', entity_type='AUTH',
            description=f"{assignee.first_name} logged in",
            metadata={'method': 'POST', 'path': '/api/auth/login/', 'status_code': 200},
            target_user=None,
        ),
    ]
    created = []
    for row in rows:
        actor = manager if row.get('target_user') else assignee
        created.append(AuditLog.objects.create(
            restaurant=rest,
            user=actor,
            action_type=row['action_type'],
            entity_type=row['entity_type'],
            entity_id=row.get('entity_id'),
            description=row['description'],
            target_user=row.get('target_user'),
            metadata=row['metadata'],
        ))
    return created


def test_agent_endpoint(manager, assignee, other_assignee):
    section("Agent endpoint — /api/agent/activity-log/")

    # Clean slate so filter tests are deterministic.
    AuditLog.objects.filter(restaurant=manager.restaurant).delete()
    entries = _seed_entries(manager, assignee, other_assignee)
    ok(f"Seeded {len(entries)} events")

    agent_key = settings.LUA_WEBHOOK_API_KEY
    client = APIClient(HTTP_HOST='localhost')
    auth = f'Bearer {agent_key}'

    # --- 1. No agent key → 401 ---
    anon = APIClient(HTTP_HOST='localhost')
    r = anon.get('/api/agent/activity-log/?restaurant_id=' + str(manager.restaurant.id))
    if r.status_code != 401:
        fail("Missing agent key should return 401", detail=f"got {r.status_code}")
    ok("No auth → 401 as expected")

    # --- 2. Missing restaurant_id → 400 ---
    r = client.get('/api/agent/activity-log/', HTTP_AUTHORIZATION=auth)
    if r.status_code != 400:
        fail("Missing restaurant_id should return 400", detail=f"got {r.status_code}")
    ok("No restaurant_id → 400 as expected")

    # --- 3. Happy path: list all events ---
    r = client.get(
        '/api/agent/activity-log/',
        {'restaurant_id': str(manager.restaurant.id)},
        HTTP_AUTHORIZATION=auth,
    )
    if r.status_code != 200:
        fail("Happy-path query failed", detail=f"status={r.status_code} body={r.content[:200]!r}")
    body = r.json()
    if not body.get('success'):
        fail("success=false in response", detail=str(body)[:200])
    if body.get('total') != len(entries):
        fail(f"total != {len(entries)}", detail=f"got total={body.get('total')}")
    ok(f"Listed all events: total={body['total']}, count={body['count']}")

    # Response row shape matches ActivityLogTool.ts expectations
    first = body['events'][0]
    required_keys = {
        'id', 'timestamp', 'action_type', 'action_label',
        'entity_type', 'entity_id', 'description',
        'user', 'target_user', 'metadata',
    }
    missing = required_keys - set(first.keys())
    if missing:
        fail(f"Event row missing keys: {sorted(missing)}")
    ok(f"Event row shape OK ({len(first.keys())} keys)")

    # --- 4. Filter by target_user_id ---
    r = client.get(
        '/api/agent/activity-log/',
        {
            'restaurant_id': str(manager.restaurant.id),
            'target_user_id': str(assignee.id),
        },
        HTTP_AUTHORIZATION=auth,
    )
    body = r.json()
    if body.get('total') != 1:
        fail("target_user_id filter returned wrong count", detail=str(body)[:200])
    if body['events'][0]['target_user']['id'] != str(assignee.id):
        fail("target_user filter returned wrong row")
    ok("target_user_id filter works (found 1 event for assignee)")

    # --- 5. Filter by entity_type (repeatable) ---
    r = client.get(
        '/api/agent/activity-log/',
        {
            'restaurant_id': str(manager.restaurant.id),
            'entity_type': ['TASK', 'SHIFT'],
        },
        HTTP_AUTHORIZATION=auth,
    )
    body = r.json()
    if body.get('total') != 2:
        fail(f"entity_type filter expected 2, got {body.get('total')}")
    ok("entity_type repeatable filter works")

    # --- 6. Filter by action_type=LOGIN ---
    r = client.get(
        '/api/agent/activity-log/',
        {
            'restaurant_id': str(manager.restaurant.id),
            'action_type': 'LOGIN',
        },
        HTTP_AUTHORIZATION=auth,
    )
    body = r.json()
    if body.get('total') != 1 or body['events'][0]['action_type'] != 'LOGIN':
        fail("action_type=LOGIN filter failed", detail=str(body)[:200])
    ok("action_type filter works")

    # --- 7. Free-text ``q`` over target user name ---
    r = client.get(
        '/api/agent/activity-log/',
        {
            'restaurant_id': str(manager.restaurant.id),
            'q': other_assignee.first_name,
        },
        HTTP_AUTHORIZATION=auth,
    )
    body = r.json()
    if body.get('total') < 1:
        fail(f"q-search on '{other_assignee.first_name}' returned 0 rows")
    ok(f"q-search found {body['total']} event(s) referencing '{other_assignee.first_name}'")

    # --- 8. days=1 shortcut (all seeded rows are fresh) ---
    r = client.get(
        '/api/agent/activity-log/',
        {
            'restaurant_id': str(manager.restaurant.id),
            'days': '1',
        },
        HTTP_AUTHORIZATION=auth,
    )
    body = r.json()
    if body.get('total') != len(entries):
        fail("days=1 should include all fresh rows", detail=str(body)[:200])
    ok("days=N shortcut works")


@override_settings(LUA_WEBHOOK_API_KEY='smoketest-key', ALLOWED_HOSTS=['*'])
def run():
    section("Setup")
    restaurant = _get_or_create_restaurant()
    manager = _get_or_create_user('memory-manager@example.com', restaurant, role='MANAGER')
    assignee = _get_or_create_user('memory-staff1@example.com', restaurant, role='STAFF')
    other_assignee = _get_or_create_user('memory-staff2@example.com', restaurant, role='STAFF')
    ok(f"Restaurant={restaurant.id}, manager={manager.id}, assignees=[{assignee.id}, {other_assignee.id}]")

    test_model_fields()
    test_middleware_enrichment(manager, assignee)
    test_agent_endpoint(manager, assignee, other_assignee)

    section("Cleanup")
    AuditLog.objects.filter(restaurant=restaurant).delete()
    ok("Cleaned up seeded audit rows")

    print("\n✅ All activity-log smoke tests passed.")


if __name__ == '__main__':
    run()
