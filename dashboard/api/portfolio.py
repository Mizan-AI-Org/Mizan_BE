"""
Portfolio summary endpoint for multi-location owners.

Unlike ``DashboardSummaryView`` which aggregates everything at the tenant
level, this endpoint returns per-``BusinessLocation`` rollups plus a
tenant-wide total. It powers the "Locations Overview" page — the owner's
command center where they see every branch at a glance.

Design notes:
- One tight pass of queries; no per-location N+1. Each domain (clock
  events, shifts, cash, checklists) is fetched once for the tenant and
  bucketed by ``location_id`` in Python.
- POS sales are deliberately omitted for now: the POS layer isn't
  branch-tagged yet. Labor cost uses ``StaffProfile.hourly_rate`` on the
  clocked-in staff.
- Every metric degrades gracefully: a tenant with one branch and no
  ``location`` FKs on its events still gets sensible numbers (they all
  bucket to the tenant's primary location).
- Cached for 60 s under a dedicated key so it doesn't poison the
  existing ``dashboard:summary`` cache and doesn't need its own
  invalidation wiring yet (short TTL is enough for an owner glance).
"""
from __future__ import annotations

import logging
import traceback
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import BusinessLocation
from core.http_caching import json_response_with_cache
from core.read_through_cache import safe_cache_get, safe_cache_set
from scheduling.models import AssignedShift, ShiftSwapRequest
from timeclock.models import CashSession, ClockEvent

logger = logging.getLogger(__name__)

# Roles that are allowed to see a detailed server-side traceback in the
# response body when the portfolio endpoint fails. This makes it possible
# to diagnose production incidents straight from the browser DevTools
# without needing access to the backend host's log stream — the whole
# point of Locations Overview is these people, and only these people.
_TRACEBACK_ROLES = {"SUPER_ADMIN", "ADMIN", "OWNER"}


PORTFOLIO_ALLOWED_ROLES = {"SUPER_ADMIN", "ADMIN", "OWNER", "MANAGER"}
PORTFOLIO_CACHE_TTL_SECONDS = 60
GRACE_MINUTES_FOR_POTENTIAL_NOSHOW = 10


def _portfolio_cache_key(restaurant_id, day) -> str:
    return f"dashboard:portfolio:v1:{restaurant_id}:{day.isoformat()}"


def _zero_metrics() -> dict[str, Any]:
    return {
        "staff_count": 0,
        "clocked_in_now": 0,
        "scheduled_today": 0,
        "coverage_pct": None,
        "no_shows_today": 0,
        "potential_no_shows": 0,
        "location_mismatches_today": 0,
        "shift_gaps_today": 0,
        "open_cash_sessions": 0,
        "flagged_cash_sessions": 0,
        "cash_variance_today": 0.0,
        "pending_swap_requests": 0,
        "checklist_completion_pct": None,
        "checklists_completed": 0,
        "checklists_total": 0,
        "labor_cost_today": 0.0,
    }


def _derive_status_and_concern(metrics: dict[str, Any]) -> tuple[str, str | None]:
    """
    Decide traffic-light colour + a single human-readable top concern.

    Ordered by severity: any red trigger wins, then amber, then green.
    Tuned for restaurant ops; thresholds deliberately conservative so
    we flag early rather than late.
    """
    no_shows = metrics["no_shows_today"]
    mismatches = metrics["location_mismatches_today"]
    flagged_cash = metrics["flagged_cash_sessions"]
    potential = metrics["potential_no_shows"]
    gaps = metrics["shift_gaps_today"]
    coverage = metrics["coverage_pct"]
    checklist_pct = metrics["checklist_completion_pct"]

    if no_shows > 0:
        return "red", f"{no_shows} no-show{'s' if no_shows != 1 else ''} today"
    if flagged_cash > 0:
        return "red", f"{flagged_cash} cash session{'s' if flagged_cash != 1 else ''} flagged"
    if mismatches > 0:
        return "red", f"{mismatches} location mismatch{'es' if mismatches != 1 else ''}"
    if coverage is not None and coverage < 50:
        return "red", f"Only {coverage}% shift coverage"

    if potential > 0:
        return "amber", f"{potential} potential no-show{'s' if potential != 1 else ''}"
    if gaps > 0:
        return "amber", f"{gaps} unfilled shift{'s' if gaps != 1 else ''}"
    if coverage is not None and coverage < 80:
        return "amber", f"{coverage}% shift coverage"
    if checklist_pct is not None and checklist_pct < 60 and metrics["checklists_total"] > 0:
        return "amber", f"{checklist_pct}% checklists done"

    return "green", None


class PortfolioSummaryView(APIView):
    """
    GET /api/dashboard/portfolio/

    Returns per-branch ops rollups plus tenant-wide totals for the
    requesting user's tenant. MANAGER callers with configured
    ``managed_locations`` see only those branches; ADMIN/SUPER_ADMIN/OWNER
    see everything.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        # Ultimate safety net: nothing in this endpoint should ever be
        # able to make the frontend render "Couldn't load portfolio data"
        # because the HTTP layer 500'd. Any uncaught exception below is
        # turned into a degraded 200 response containing at least the
        # list of branches and — for admins — the traceback so we can
        # debug from the browser.
        try:
            return self._get_inner(request)
        except Exception as exc:
            logger.exception("portfolio: top-level handler failed")
            user = getattr(request, "user", None)
            role = getattr(user, "role", None) if user else None
            restaurant = getattr(user, "restaurant", None) if user else None
            today = timezone.now().date()
            payload = self._safe_fallback(
                restaurant, user, role, today, exc
            ) if restaurant else {
                "generated_at": timezone.now().isoformat(),
                "today": today.isoformat(),
                "tenant": {"id": None, "name": ""},
                "totals": _zero_metrics(),
                "locations": [],
                "degraded": True,
                "error": "Portfolio endpoint hit an unexpected error: "
                + str(exc)[:200],
            }
            if role in _TRACEBACK_ROLES:
                payload["traceback"] = traceback.format_exc()[-2000:]
            return Response(payload, status=200)

    def _get_inner(self, request):
        user = request.user
        restaurant = getattr(user, "restaurant", None)
        if restaurant is None:
            return Response({"error": "No workspace associated"}, status=400)

        role = getattr(user, "role", None)
        if role not in PORTFOLIO_ALLOWED_ROLES:
            return Response({"error": "Forbidden"}, status=403)

        today = timezone.now().date()
        cache_key = _portfolio_cache_key(restaurant.id, today)
        cached = safe_cache_get(cache_key)
        # Serve cache only for unscoped callers; scoped managers have their
        # own view of the data so we skip cache for them to avoid leaking.
        # Defensive: if the user model doesn't have managed_locations yet
        # (rare, mid-deploy), treat them as unscoped instead of 500'ing.
        try:
            is_scoped_manager = (
                role == "MANAGER" and user.managed_locations.exists()
            )
        except Exception as exc:
            logger.warning(
                "portfolio: managed_locations lookup failed for user=%s: %s",
                getattr(user, "id", None),
                exc,
            )
            is_scoped_manager = False

        if cached is not None and not is_scoped_manager:
            # Add ETag/Cache-Control so a polling client sending the same
            # If-None-Match gets a cheap 304 instead of the full payload.
            return json_response_with_cache(
                request,
                cached,
                max_age=PORTFOLIO_CACHE_TTL_SECONDS,
                private=True,
                stale_while_revalidate=120,
            )

        try:
            payload = self._compute(restaurant, user, role, today)
        except Exception as exc:
            # Log the full traceback so we can diagnose, but still hand the
            # caller a useful response: at minimum the list of branches and
            # an error message they can show in the UI. This is what stops
            # "Locations Overview" going completely blank when one of the
            # downstream queries blows up (e.g. a missing migration on a
            # joined table).
            logger.exception(
                "portfolio: _compute failed for restaurant=%s, falling back to locations-only payload",
                restaurant.id,
            )
            payload = self._safe_fallback(restaurant, user, role, today, exc)
            if role in _TRACEBACK_ROLES:
                payload["traceback"] = traceback.format_exc()[-2000:]
            return Response(payload, status=200)

        if not is_scoped_manager:
            safe_cache_set(cache_key, payload, PORTFOLIO_CACHE_TTL_SECONDS)
        return json_response_with_cache(
            request,
            payload,
            max_age=PORTFOLIO_CACHE_TTL_SECONDS,
            private=True,
            stale_while_revalidate=120,
        )

    def _safe_fallback(self, restaurant, user, role, today, exc) -> dict[str, Any]:
        """
        Build a degraded-but-useful response when the full metrics compute
        fails. We never want the Locations Overview to render an empty
        page just because one downstream domain (POS, scheduling, …) blew
        up — the owner still needs to see every branch they have.
        """
        try:
            locations_qs = BusinessLocation.objects.filter(
                restaurant=restaurant, is_active=True
            )
            if role == "MANAGER":
                try:
                    managed_ids = list(
                        user.managed_locations.values_list("id", flat=True)
                    )
                    if managed_ids:
                        locations_qs = locations_qs.filter(id__in=managed_ids)
                except Exception:
                    pass
            locations = list(locations_qs.order_by("-is_primary", "name"))
        except Exception:
            locations = []

        locations_payload = [
            {
                "id": str(loc.id),
                "name": loc.name,
                "is_primary": loc.is_primary,
                "is_active": loc.is_active,
                "status": "unknown",
                "top_concern": None,
                "metrics": _zero_metrics(),
            }
            for loc in locations
        ]
        return {
            "generated_at": timezone.now().isoformat(),
            "today": today.isoformat(),
            "tenant": {
                "id": str(restaurant.id),
                "name": getattr(restaurant, "name", ""),
            },
            "totals": _zero_metrics(),
            "locations": locations_payload,
            "degraded": True,
            "error": (
                "Some live metrics could not be computed; showing branches "
                "without today's numbers. Detail: " + str(exc)[:200]
            ),
        }

    def _compute(self, restaurant, user, role, today) -> dict[str, Any]:
        now = timezone.now()

        locations_qs = BusinessLocation.objects.filter(
            restaurant=restaurant, is_active=True
        )
        if role == "MANAGER":
            managed_ids = list(
                user.managed_locations.values_list("id", flat=True)
            )
            if managed_ids:
                locations_qs = locations_qs.filter(id__in=managed_ids)
        locations = list(locations_qs.order_by("-is_primary", "name"))

        # Primary location id is our fallback bucket for rows whose
        # location FK is null (legacy events, single-site tenants that
        # never set location on their shifts, etc.).
        primary_location = next(
            (loc for loc in locations if loc.is_primary), locations[0] if locations else None
        )
        primary_id = primary_location.id if primary_location else None
        known_ids = {loc.id for loc in locations}

        def bucket_for(loc_id):
            """Return the location id this row should count against,
            falling back to primary when unknown/null."""
            if loc_id in known_ids:
                return loc_id
            return primary_id

        # Per-location metric buckets (initialised so every branch always
        # appears in the response, even if it had zero activity today).
        metrics_by_loc: dict[Any, dict[str, Any]] = {
            loc.id: _zero_metrics() for loc in locations
        }

        # Pair events per staff to derive "still clocked in" and hours.
        # One staff may clock in at branch A and out at branch B; we
        # attribute labour cost to the branch of the FIRST in of the day.
        per_staff: dict[Any, dict[str, Any]] = defaultdict(
            lambda: {
                "first_in": None,
                "last_out": None,
                "location_of_first_in": None,
                "hourly_rate": Decimal("0"),
                "mismatched": False,
            }
        )

        # ---- Clock events today -> clocked_in_now, mismatches, labor cost
        try:
            clock_events = list(
                ClockEvent.objects.filter(
                    staff__restaurant=restaurant,
                    timestamp__date=today,
                )
                .select_related("staff", "staff__profile", "location")
                .order_by("staff_id", "timestamp")
            )

            mismatches_by_loc: dict[Any, int] = defaultdict(int)

            for ev in clock_events:
                sid = ev.staff_id
                if sid is None:
                    continue
                evt = (ev.event_type or "").lower()
                is_in = evt in ("in", "clock_in")
                is_out = evt in ("out", "clock_out")
                slot = per_staff[sid]
                if is_in and slot["first_in"] is None:
                    slot["first_in"] = ev.timestamp
                    slot["location_of_first_in"] = bucket_for(ev.location_id)
                    profile = getattr(ev.staff, "profile", None)
                    slot["hourly_rate"] = (
                        profile.hourly_rate if profile and profile.hourly_rate else Decimal("0")
                    )
                if is_out:
                    slot["last_out"] = ev.timestamp
                if ev.location_mismatch:
                    b = bucket_for(ev.location_id)
                    if b is not None:
                        mismatches_by_loc[b] += 1

            for sid, slot in per_staff.items():
                loc_id = slot["location_of_first_in"]
                if loc_id not in metrics_by_loc:
                    continue
                bucket = metrics_by_loc[loc_id]
                # Currently clocked in = had an 'in' and no matching 'out'
                # after it. We approximate with "no last_out" which is fine
                # for today's snapshot.
                if slot["first_in"] is not None and slot["last_out"] is None:
                    bucket["clocked_in_now"] += 1
                    end_time = now
                else:
                    end_time = slot["last_out"] or now
                if slot["first_in"]:
                    hours = max(0.0, (end_time - slot["first_in"]).total_seconds() / 3600.0)
                    bucket["labor_cost_today"] += round(hours * float(slot["hourly_rate"]), 2)

            for loc_id, count in mismatches_by_loc.items():
                if loc_id in metrics_by_loc:
                    metrics_by_loc[loc_id]["location_mismatches_today"] = count
        except Exception:
            logger.exception("portfolio: clock-event aggregation failed; skipping")

        # ---- Shifts today -> scheduled, no-shows, potential no-shows, gaps
        try:
            shifts_today = AssignedShift.objects.filter(
                schedule__restaurant=restaurant,
                shift_date=today,
            ).only(
                "id", "status", "start_time", "staff_id", "location_id"
            )

            staff_clocked_in_today = {
                sid for sid, slot in per_staff.items() if slot["first_in"] is not None
            }
            grace_cutoff = now - timedelta(minutes=GRACE_MINUTES_FOR_POTENTIAL_NOSHOW)

            for s in shifts_today:
                loc_id = bucket_for(s.location_id)
                if loc_id not in metrics_by_loc:
                    continue
                bucket = metrics_by_loc[loc_id]
                if s.staff_id is not None:
                    bucket["scheduled_today"] += 1
                if s.status == "NO_SHOW":
                    bucket["no_shows_today"] += 1
                elif (
                    s.status in ("SCHEDULED", "CONFIRMED")
                    and s.staff_id is not None
                    and s.start_time is not None
                    and s.start_time <= grace_cutoff
                    and s.staff_id not in staff_clocked_in_today
                ):
                    bucket["potential_no_shows"] += 1
        except Exception:
            logger.exception("portfolio: shift aggregation failed; skipping")

        # ---- Shift gaps (assigned shifts with no staff assigned at all)
        try:
            shifts_with_staff_counts = AssignedShift.objects.filter(
                schedule__restaurant=restaurant,
                shift_date=today,
                status__in=["SCHEDULED", "CONFIRMED"],
            ).annotate(members_count=Count("staff_members"))

            for s in shifts_with_staff_counts.only("id", "staff_id", "location_id"):
                if s.staff_id is None and s.members_count == 0:
                    loc_id = bucket_for(s.location_id)
                    if loc_id in metrics_by_loc:
                        metrics_by_loc[loc_id]["shift_gaps_today"] += 1
        except Exception:
            logger.exception("portfolio: shift-gap aggregation failed; skipping")

        # ---- Cash sessions today
        try:
            cash_today = CashSession.objects.filter(
                restaurant=restaurant, session_date=today
            ).select_related("shift", "staff", "staff__primary_location")

            for cs in cash_today:
                # Prefer the shift's branch; fall back to the staff member's
                # primary_location; fall back to tenant primary.
                loc_id = None
                if cs.shift_id and cs.shift and cs.shift.location_id:
                    loc_id = cs.shift.location_id
                elif cs.staff and getattr(cs.staff, "primary_location_id", None):
                    loc_id = cs.staff.primary_location_id
                loc_id = bucket_for(loc_id)
                if loc_id not in metrics_by_loc:
                    continue
                bucket = metrics_by_loc[loc_id]
                if cs.status in ("OPEN", "COUNTED"):
                    bucket["open_cash_sessions"] += 1
                if cs.status == "FLAGGED":
                    bucket["flagged_cash_sessions"] += 1
                if cs.variance is not None:
                    bucket["cash_variance_today"] = round(
                        bucket["cash_variance_today"] + float(cs.variance), 2
                    )
        except Exception:
            logger.exception("portfolio: cash-session aggregation failed; skipping")

        # ---- Pending swap requests (tenant-wide, bucketed by shift loc)
        try:
            swaps = ShiftSwapRequest.objects.filter(
                shift_to_swap__schedule__restaurant=restaurant,
                status="PENDING",
            ).values("shift_to_swap__location_id").annotate(n=Count("id"))

            for row in swaps:
                loc_id = bucket_for(row["shift_to_swap__location_id"])
                if loc_id in metrics_by_loc:
                    metrics_by_loc[loc_id]["pending_swap_requests"] += row["n"]
        except Exception:
            logger.exception("portfolio: swap-request aggregation failed; skipping")

        # ---- Checklist completion today (shift checklist progress)
        try:
            from scheduling.models import ShiftChecklistProgress

            progress_qs = (
                ShiftChecklistProgress.objects.filter(
                    shift__schedule__restaurant=restaurant,
                    shift__shift_date=today,
                )
                .values("shift__location_id", "status")
                .annotate(n=Count("id"))
            )
            for row in progress_qs:
                loc_id = bucket_for(row["shift__location_id"])
                if loc_id not in metrics_by_loc:
                    continue
                bucket = metrics_by_loc[loc_id]
                bucket["checklists_total"] += row["n"]
                if row["status"] == "COMPLETED":
                    bucket["checklists_completed"] += row["n"]
        except Exception:
            # Checklists are optional per tenant — never let them
            # break the portfolio view.
            pass

        # ---- Staff count per branch (primary_location is the home branch)
        # If `primary_location_id` doesn't exist on the deployed schema yet
        # (mid-deploy / migration not applied), fall back to bucketing every
        # active staff to the tenant primary so the column at least renders.
        try:
            from accounts.models import CustomUser

            staff_rows = (
                CustomUser.objects.filter(
                    restaurant=restaurant, is_active=True
                )
                .exclude(role="SUPER_ADMIN")
                .values("primary_location_id")
                .annotate(n=Count("id"))
            )
            for row in staff_rows:
                loc_id = bucket_for(row["primary_location_id"])
                if loc_id in metrics_by_loc:
                    metrics_by_loc[loc_id]["staff_count"] += row["n"]
        except Exception:
            logger.exception(
                "portfolio: per-branch staff count failed; falling back to tenant total on primary"
            )
            try:
                from accounts.models import CustomUser

                total_staff = (
                    CustomUser.objects.filter(
                        restaurant=restaurant, is_active=True
                    )
                    .exclude(role="SUPER_ADMIN")
                    .count()
                )
                if primary_id and primary_id in metrics_by_loc:
                    metrics_by_loc[primary_id]["staff_count"] += total_staff
            except Exception:
                logger.exception("portfolio: tenant staff count fallback also failed")

        # ---- Derive coverage %, checklist %, and status per branch
        locations_payload: list[dict[str, Any]] = []
        for loc in locations:
            m = metrics_by_loc[loc.id]
            if m["scheduled_today"] > 0:
                m["coverage_pct"] = int(
                    round(100.0 * m["clocked_in_now"] / m["scheduled_today"])
                )
            if m["checklists_total"] > 0:
                m["checklist_completion_pct"] = int(
                    round(100.0 * m["checklists_completed"] / m["checklists_total"])
                )
            status, concern = _derive_status_and_concern(m)
            locations_payload.append(
                {
                    "id": str(loc.id),
                    "name": loc.name,
                    "is_primary": loc.is_primary,
                    "is_active": loc.is_active,
                    "status": status,
                    "top_concern": concern,
                    "metrics": m,
                }
            )

        totals = self._aggregate_totals(metrics_by_loc.values())

        return {
            "generated_at": now.isoformat(),
            "today": today.isoformat(),
            "tenant": {"id": str(restaurant.id), "name": getattr(restaurant, "name", "")},
            "totals": totals,
            "locations": locations_payload,
        }

    @staticmethod
    def _aggregate_totals(per_loc_metrics) -> dict[str, Any]:
        agg = _zero_metrics()
        # Checklists are summed, not averaged, so we recompute the
        # percentage once at the end from the grand totals.
        clocked_in_now = 0
        scheduled_today = 0
        for m in per_loc_metrics:
            clocked_in_now += m["clocked_in_now"]
            scheduled_today += m["scheduled_today"]
            for k in (
                "staff_count",
                "no_shows_today",
                "potential_no_shows",
                "location_mismatches_today",
                "shift_gaps_today",
                "open_cash_sessions",
                "flagged_cash_sessions",
                "pending_swap_requests",
                "checklists_completed",
                "checklists_total",
            ):
                agg[k] += m[k]
            agg["cash_variance_today"] = round(
                agg["cash_variance_today"] + m["cash_variance_today"], 2
            )
            agg["labor_cost_today"] = round(
                agg["labor_cost_today"] + m["labor_cost_today"], 2
            )
        agg["clocked_in_now"] = clocked_in_now
        agg["scheduled_today"] = scheduled_today
        if scheduled_today > 0:
            agg["coverage_pct"] = int(round(100.0 * clocked_in_now / scheduled_today))
        if agg["checklists_total"] > 0:
            agg["checklist_completion_pct"] = int(
                round(100.0 * agg["checklists_completed"] / agg["checklists_total"])
            )
        return agg


class LocationDetailView(APIView):
    """
    GET /api/dashboard/portfolio/locations/<uuid:loc_id>/

    Per-branch deep-dive for the Locations Overview drill-in. Returns the
    same metrics as PortfolioSummaryView for one location, plus today's
    raw activity (shifts, clock events, mismatches, cash sessions) so the
    owner can see why a branch is green/amber/red without leaving the page.

    MANAGER callers can only access branches in their managed_locations.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, loc_id):
        user = request.user
        restaurant = getattr(user, "restaurant", None)
        if restaurant is None:
            return Response({"error": "No workspace associated"}, status=400)

        role = getattr(user, "role", None)
        if role not in PORTFOLIO_ALLOWED_ROLES:
            return Response({"error": "Forbidden"}, status=403)

        try:
            location = BusinessLocation.objects.get(
                id=loc_id, restaurant=restaurant
            )
        except BusinessLocation.DoesNotExist:
            return Response({"error": "Location not found"}, status=404)

        if role == "MANAGER":
            managed_ids = set(
                str(i) for i in user.managed_locations.values_list("id", flat=True)
            )
            if managed_ids and str(location.id) not in managed_ids:
                return Response({"error": "Forbidden"}, status=403)

        # Reuse the portfolio compute so a single branch's metrics here
        # match exactly what's shown on the overview page (no drift).
        portfolio = PortfolioSummaryView()
        full = portfolio._compute(restaurant, user, role, timezone.now().date())
        row = next(
            (r for r in full["locations"] if str(r["id"]) == str(location.id)),
            None,
        )
        if row is None:
            return Response({"error": "Location not in scope"}, status=404)

        today = timezone.now().date()
        details = self._collect_today(restaurant, location, today)

        return Response(
            {
                "generated_at": full["generated_at"],
                "today": full["today"],
                "tenant": full["tenant"],
                "location": row,
                **details,
            }
        )

    def _collect_today(self, restaurant, location, today) -> dict[str, Any]:
        """
        Pull today's raw activity for one branch. Capped per list so a
        very busy branch doesn't blow up the response; deep links on the
        page point at the proper scoped reports for the long tail.
        """
        ROW_CAP = 50
        primary = BusinessLocation.objects.filter(
            restaurant=restaurant, is_primary=True
        ).first()
        is_primary_branch = primary and primary.id == location.id

        # ---- Today's shifts at this branch (or unscoped shifts if primary)
        shift_filter = Q(location_id=location.id)
        if is_primary_branch:
            shift_filter = shift_filter | Q(location__isnull=True)
        shifts = (
            AssignedShift.objects.filter(
                schedule__restaurant=restaurant,
                shift_date=today,
            )
            .filter(shift_filter)
            .select_related("staff")
            .order_by("start_time")[:ROW_CAP]
        )
        shifts_payload = [
            {
                "id": str(s.id),
                "staff_name": (
                    f"{s.staff.first_name} {s.staff.last_name}".strip()
                    if s.staff_id
                    else "Unassigned"
                ),
                "role": s.role or "",
                "status": s.status,
                "start_time": s.start_time.isoformat() if s.start_time else None,
                "end_time": s.end_time.isoformat() if s.end_time else None,
            }
            for s in shifts
        ]

        # ---- Today's clock events at this branch
        clock_filter = Q(location_id=location.id)
        if is_primary_branch:
            clock_filter = clock_filter | Q(location__isnull=True)
        events = (
            ClockEvent.objects.filter(
                staff__restaurant=restaurant,
                timestamp__date=today,
            )
            .filter(clock_filter)
            .select_related("staff", "location")
            .order_by("-timestamp")[:ROW_CAP]
        )
        events_payload = [
            {
                "id": str(ev.id),
                "staff_name": (
                    f"{ev.staff.first_name} {ev.staff.last_name}".strip()
                    if ev.staff_id
                    else "—"
                ),
                "event_type": ev.event_type,
                "timestamp": ev.timestamp.isoformat(),
                "location_mismatch": bool(ev.location_mismatch),
            }
            for ev in events
        ]

        # ---- Today's cash sessions touching this branch
        cash_qs = CashSession.objects.filter(
            restaurant=restaurant, session_date=today
        ).select_related("shift", "staff", "staff__primary_location")
        cash_payload: list[dict[str, Any]] = []
        for cs in cash_qs:
            loc_id = None
            if cs.shift_id and cs.shift and cs.shift.location_id:
                loc_id = cs.shift.location_id
            elif cs.staff and cs.staff.primary_location_id:
                loc_id = cs.staff.primary_location_id
            # Bucket unknown to primary so the primary branch sees them.
            belongs_here = (
                loc_id == location.id
                or (loc_id is None and is_primary_branch)
            )
            if not belongs_here:
                continue
            cash_payload.append(
                {
                    "id": str(cs.id),
                    "staff_name": (
                        f"{cs.staff.first_name} {cs.staff.last_name}".strip()
                        if cs.staff_id
                        else "—"
                    ),
                    "status": cs.status,
                    "variance": float(cs.variance) if cs.variance is not None else None,
                    "opening_float": (
                        float(cs.opening_float) if cs.opening_float is not None else None
                    ),
                    "counted_cash": (
                        float(cs.counted_cash) if cs.counted_cash is not None else None
                    ),
                    "expected_cash": (
                        float(cs.expected_cash) if cs.expected_cash is not None else None
                    ),
                }
            )
            if len(cash_payload) >= ROW_CAP:
                break

        return {
            "shifts_today": shifts_payload,
            "clock_events_today": events_payload,
            "cash_sessions_today": cash_payload,
        }
