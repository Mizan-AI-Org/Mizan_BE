"""
Unit tests for the Miya agent cache gaps filled in this change.

These are ``SimpleTestCase`` tests (no DB) exercising the cache-key
helpers and invalidation fan-out so we can verify the cache shape,
TTL plumbing, and invalidation contract without spinning up a
Postgres (which keeps them runnable locally without DB creds and
fast in CI).

What we verify:

* ``get_or_set`` calls the factory exactly once per (key, TTL) and
  serves subsequent lookups from the cache.
* Invalidator helpers delete the expected keys so the next lookup
  re-invokes the factory instead of serving the stale hit.
* Cache keys are namespaced per-tenant so a bust for restaurant A
  can never evict restaurant B's slice.
"""
from __future__ import annotations

import uuid

from django.core.cache import cache
from django.test import SimpleTestCase

from core.read_through_cache import get_or_set


class StaffAgentCacheHelpersTests(SimpleTestCase):
    """staff.views_agent cache-key helpers."""

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_staff_requests_cache_key_is_tenant_scoped(self):
        from staff.views_agent import _staff_requests_cache_key

        rid_a = uuid.uuid4()
        rid_b = uuid.uuid4()
        k_a = _staff_requests_cache_key(rid_a, "PENDING")
        k_b = _staff_requests_cache_key(rid_b, "PENDING")
        self.assertNotEqual(k_a, k_b)
        # Different status filters should never collide either.
        self.assertNotEqual(
            _staff_requests_cache_key(rid_a, "PENDING"),
            _staff_requests_cache_key(rid_a, "APPROVED"),
        )
        # Same inputs are stable across calls.
        self.assertEqual(k_a, _staff_requests_cache_key(rid_a, "PENDING"))

    def test_invalidate_staff_requests_wipes_every_status_slice(self):
        from staff.views_agent import (
            _invalidate_staff_requests_cache,
            _staff_requests_cache_key,
        )

        rid = uuid.uuid4()
        for sf in ("PENDING", "APPROVED", "REJECTED", "ALL"):
            cache.set(_staff_requests_cache_key(rid, sf), {"sentinel": sf}, 60)
            self.assertIsNotNone(cache.get(_staff_requests_cache_key(rid, sf)))
        _invalidate_staff_requests_cache(rid)
        for sf in ("PENDING", "APPROVED", "REJECTED", "ALL"):
            self.assertIsNone(
                cache.get(_staff_requests_cache_key(rid, sf)),
                msg=f"slice {sf} should have been wiped",
            )

    def test_invalidate_staff_requests_leaves_other_tenants_untouched(self):
        from staff.views_agent import (
            _invalidate_staff_requests_cache,
            _staff_requests_cache_key,
        )

        rid_a, rid_b = uuid.uuid4(), uuid.uuid4()
        cache.set(_staff_requests_cache_key(rid_a, "PENDING"), {"a": 1}, 60)
        cache.set(_staff_requests_cache_key(rid_b, "PENDING"), {"b": 1}, 60)
        _invalidate_staff_requests_cache(rid_a)
        self.assertIsNone(cache.get(_staff_requests_cache_key(rid_a, "PENDING")))
        self.assertEqual(
            cache.get(_staff_requests_cache_key(rid_b, "PENDING")), {"b": 1}
        )

    def test_invalidate_incidents_wipes_status_slices(self):
        from staff.views_agent import (
            _invalidate_staff_incidents_cache,
            _staff_incidents_cache_key,
        )

        rid = uuid.uuid4()
        for sf in ("OPEN", "RESOLVED", "UNDER_REVIEW", "ESCALATED"):
            cache.set(_staff_incidents_cache_key(rid, sf), {"x": sf}, 60)
        _invalidate_staff_incidents_cache(rid)
        for sf in ("OPEN", "RESOLVED", "UNDER_REVIEW", "ESCALATED"):
            self.assertIsNone(cache.get(_staff_incidents_cache_key(rid, sf)))


class FinanceInvoicesCacheHelpersTests(SimpleTestCase):
    """finance.views_agent cache-key + invalidator helpers."""

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_invoices_cache_key_hashes_filter_tuple(self):
        from finance.views_agent import _invoices_cache_key

        rid = uuid.uuid4()
        k1 = _invoices_cache_key(rid, ("OPEN", "", 0, None, 25, "2026-05-01"))
        k2 = _invoices_cache_key(rid, ("OPEN", "", 0, None, 25, "2026-05-01"))
        k3 = _invoices_cache_key(rid, ("OPEN", "", 1, None, 25, "2026-05-01"))
        self.assertEqual(k1, k2, "stable across calls for same filters")
        self.assertNotEqual(k1, k3, "changing overdue flag must change the key")
        # Different tenant, same filters → different key.
        self.assertNotEqual(
            k1, _invoices_cache_key(uuid.uuid4(), ("OPEN", "", 0, None, 25, "2026-05-01"))
        )

    def test_invalidate_invoices_wipes_tracked_slices(self):
        from finance.views_agent import (
            _invoices_cache_key,
            _remember_invoices_cache_key,
            invalidate_invoices_cache,
        )

        rid = uuid.uuid4()
        keys = [
            _invoices_cache_key(rid, ("OPEN", "", 0, None, 25, "2026-05-01")),
            _invoices_cache_key(rid, ("PAID", "", 0, None, 25, "2026-05-01")),
            _invoices_cache_key(rid, ("ALL", "vendor", 1, 7, 50, "2026-05-01")),
        ]
        for k in keys:
            cache.set(k, {"payload": k}, 60)
            _remember_invoices_cache_key(rid, k)

        invalidate_invoices_cache(rid)
        for k in keys:
            self.assertIsNone(cache.get(k), msg=f"{k} should have been wiped")


class TimeclockAttendanceCacheHelpersTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_attendance_report_cache_key_is_per_tenant_per_date(self):
        from timeclock.views import _attendance_report_cache_key

        rid_a, rid_b = uuid.uuid4(), uuid.uuid4()
        self.assertNotEqual(
            _attendance_report_cache_key(rid_a, "2026-05-01"),
            _attendance_report_cache_key(rid_b, "2026-05-01"),
        )
        self.assertNotEqual(
            _attendance_report_cache_key(rid_a, "2026-05-01"),
            _attendance_report_cache_key(rid_a, "2026-05-02"),
        )

    def test_invalidate_attendance_report_wipes_today_and_given_date(self):
        from timeclock.views import (
            _attendance_report_cache_key,
            invalidate_attendance_report,
        )
        from django.utils import timezone

        rid = uuid.uuid4()
        today_iso = timezone.now().date().isoformat()
        explicit_iso = "2026-04-15"
        cache.set(_attendance_report_cache_key(rid, today_iso), {"t": 1}, 60)
        cache.set(_attendance_report_cache_key(rid, explicit_iso), {"e": 1}, 60)

        invalidate_attendance_report(rid, explicit_iso)
        self.assertIsNone(cache.get(_attendance_report_cache_key(rid, today_iso)))
        self.assertIsNone(cache.get(_attendance_report_cache_key(rid, explicit_iso)))


class StaffMessagesRecentCacheHelpersTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_recent_cache_key_is_scoped_per_limit(self):
        from dashboard.api.staff_messages import _recent_cache_key

        rid = uuid.uuid4()
        self.assertNotEqual(
            _recent_cache_key(rid, 10),
            _recent_cache_key(rid, 25),
        )

    def test_invalidate_recent_cache_wipes_all_standard_limits(self):
        from dashboard.api.staff_messages import (
            DEFAULT_LIMIT,
            MAX_LIMIT,
            _invalidate_recent_cache,
            _recent_cache_key,
        )

        rid = uuid.uuid4()
        for lim in (DEFAULT_LIMIT, 25, MAX_LIMIT):
            cache.set(_recent_cache_key(rid, lim), {"lim": lim}, 60)
        _invalidate_recent_cache(rid)
        for lim in (DEFAULT_LIMIT, 25, MAX_LIMIT):
            self.assertIsNone(
                cache.get(_recent_cache_key(rid, lim)),
                msg=f"limit {lim} slice should be wiped",
            )


class GetOrSetSemanticsTests(SimpleTestCase):
    """Smoke-test the shared ``get_or_set`` helper — the single primitive
    every new cache above leans on — so we catch regressions to the
    read-through contract quickly."""

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_factory_runs_once_on_cold_cache(self):
        calls = {"n": 0}

        def factory():
            calls["n"] += 1
            return {"value": calls["n"]}

        a = get_or_set("unit:test:get_or_set:a", 30, factory)
        b = get_or_set("unit:test:get_or_set:a", 30, factory)
        self.assertEqual(a, {"value": 1})
        self.assertEqual(b, {"value": 1})
        self.assertEqual(calls["n"], 1, "factory should have been called once")

    def test_factory_reruns_after_delete(self):
        calls = {"n": 0}

        def factory():
            calls["n"] += 1
            return {"value": calls["n"]}

        get_or_set("unit:test:get_or_set:b", 30, factory)
        cache.delete("unit:test:get_or_set:b")
        get_or_set("unit:test:get_or_set:b", 30, factory)
        self.assertEqual(calls["n"], 2)
