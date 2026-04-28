"""
Cross-location summary endpoint for Miya.

Gives the agent a one-call answer to "how is my Casablanca branch doing
versus my Marrakech branch?" without dragging the full portfolio
payload (which is denser than what Miya needs in a chat reply).

For each active ``BusinessLocation`` we return:
- staff_total (active users in the tenant assigned to that location)
- clocked_in_now (open ClockEvent with no matching OUT today)
- clock_in_count_today
- open_requests: total + by priority (URGENT/HIGH/MEDIUM/LOW)
- waiting_on_count

Plus a tenant-wide ``totals`` object so Miya can answer either
question shape ("which branch...?" / "across all my branches...?").

Auth: same agent / JWT chain as every other ``/api/.../agent/`` view —
``Authorization: Bearer <LUA_WEBHOOK_API_KEY>`` or a user JWT.

Period selector accepts ``today`` (default), ``week`` (7-day rolling),
``month`` (30-day rolling). Anything else falls back to today.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta
from typing import Any

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from accounts.models import BusinessLocation, CustomUser
from staff.models import StaffRequest
from timeclock.models import ClockEvent

logger = logging.getLogger(__name__)


_OPEN_STAFF_REQUEST_STATUSES = ("PENDING", "ESCALATED", "APPROVED", "WAITING_ON")


def _normalise_period(raw: str | None) -> tuple[str, int]:
    """Return (period_label, days) for the requested period."""
    raw_norm = (raw or "today").strip().lower()
    if raw_norm in ("week", "7d", "last_week", "7_days"):
        return "week", 7
    if raw_norm in ("month", "30d", "last_month", "30_days"):
        return "month", 30
    return "today", 1


def _format_summary_message(period_label: str, totals: dict, locations_payload: list) -> str:
    """One-line natural language summary Miya can voice straight back."""
    if not locations_payload:
        return f"No active branches to compare for {period_label}."

    open_total = totals.get("open_requests_total") or 0
    clocked = totals.get("clocked_in_now") or 0
    pieces = [
        f"Across {len(locations_payload)} branch{'es' if len(locations_payload) != 1 else ''} ({period_label})",
        f"{clocked} clocked-in right now",
        f"{open_total} open request{'s' if open_total != 1 else ''}",
    ]
    if totals.get("urgent_open"):
        pieces.append(f"{totals['urgent_open']} URGENT")
    base = "; ".join(pieces) + "."

    # Highlight the busiest branch (most open work right now).
    busiest = max(locations_payload, key=lambda r: r.get("open_requests_total", 0))
    if (busiest.get("open_requests_total") or 0) > 0:
        base += (
            f" Busiest: {busiest['name']} with "
            f"{busiest['open_requests_total']} open request"
            f"{'s' if busiest['open_requests_total'] != 1 else ''}"
            + (f" ({busiest['urgent_open']} urgent)" if busiest.get('urgent_open') else "")
            + "."
        )
    return base


@api_view(["GET", "POST"])
@authentication_classes([])  # Bypass JWT; we manually validate via _resolve_restaurant_for_agent
@permission_classes([permissions.AllowAny])
def agent_cross_location_report(request):
    """
    GET/POST /api/dashboard/agent/cross-location-report/

    Query / body params:
        period: 'today' | 'week' | 'month'  (default 'today')
        restaurant_id: optional override

    Returns:
        {
          "success": true,
          "period": "today",
          "generated_at": "...",
          "tenant": {"id": "...", "name": "..."},
          "locations": [
            {
              "id": "...", "name": "Casablanca", "is_primary": true,
              "staff_total": 24,
              "clocked_in_now": 8,
              "clock_in_count_today": 14,
              "open_requests_total": 5,
              "open_by_priority": {"URGENT": 1, "HIGH": 2, "MEDIUM": 2, "LOW": 0},
              "urgent_open": 1,
              "waiting_on_count": 1
            }, ...
          ],
          "totals": { ...same shape... },
          "message_for_user": "Across 3 branches (today); 12 clocked-in right now; 9 open requests; 2 URGENT. Busiest: Casablanca with 5 open requests (1 urgent)."
        }
    """
    # Lazy import — same circular-avoidance pattern as dashboard.views_agent.
    from scheduling.views_agent import _resolve_restaurant_for_agent

    restaurant, acting_user, err = _resolve_restaurant_for_agent(request)
    if err:
        return Response(
            {"success": False, "error": err["error"]},
            status=err["status"],
        )

    raw_period = (
        request.query_params.get("period")
        if request.method == "GET"
        else (request.data.get("period") if isinstance(getattr(request, "data", None), dict) else None)
    )
    period_label, period_days = _normalise_period(raw_period)

    now = timezone.now()
    today = now.date()
    period_start = today - timedelta(days=period_days - 1)

    locations = list(
        BusinessLocation.objects.filter(restaurant=restaurant, is_active=True)
        .order_by("-is_primary", "name")
    )

    # If a manager is restricted to specific managed_locations, scope to
    # those. For agent-key calls there is no acting user, so we return
    # everything (Miya has tenant-wide access anyway).
    if acting_user is not None and getattr(acting_user, "role", None) == "MANAGER":
        try:
            managed_ids = set(acting_user.managed_locations.values_list("id", flat=True))
            if managed_ids:
                locations = [loc for loc in locations if loc.id in managed_ids]
        except Exception:
            # Safe fallback — managed_locations relation isn't critical
            # for this read-only summary.
            pass

    if not locations:
        empty_payload = {
            "success": True,
            "period": period_label,
            "generated_at": now.isoformat(),
            "tenant": {"id": str(restaurant.id), "name": restaurant.name},
            "locations": [],
            "totals": _zero_loc_metrics(),
            "message_for_user": (
                f"{restaurant.name} doesn't have any active branches yet, "
                "so there's nothing to compare."
            ),
        }
        return Response(empty_payload, status=status.HTTP_200_OK)

    primary_location = next((l for l in locations if l.is_primary), locations[0])
    known_ids = {loc.id for loc in locations}

    def bucket_for(loc_id):
        # Rows with a NULL location FK still need to count somewhere —
        # we attribute them to the tenant's primary branch (matches the
        # existing portfolio aggregator's behaviour).
        if loc_id in known_ids:
            return loc_id
        return primary_location.id

    # ── Staff per location ──
    # Users belong to a Restaurant, not a BusinessLocation directly. We
    # use ``managed_locations`` (where set) plus the ``primary_location``
    # FK on the user (if the model has one) and otherwise treat them as
    # tenant-wide. The simplest correct attribution: count active users
    # against their primary managed location, falling back to the
    # tenant's primary. This matches what managers see in the UI.
    staff_count_by_loc: dict[Any, int] = defaultdict(int)
    user_qs = (
        CustomUser.objects.filter(restaurant=restaurant, is_active=True)
        .only("id", "primary_location_id" if hasattr(CustomUser, "primary_location") else "id")
    )
    has_primary_location_fk = hasattr(CustomUser, "primary_location_id") or hasattr(CustomUser, "primary_location")
    for u in user_qs.iterator(chunk_size=500):
        loc_id = getattr(u, "primary_location_id", None) if has_primary_location_fk else None
        staff_count_by_loc[bucket_for(loc_id)] += 1

    # ── Clock events today ──
    clocked_in_now_by_loc: dict[Any, int] = defaultdict(int)
    clock_in_count_period_by_loc: dict[Any, int] = defaultdict(int)
    try:
        period_clock_events = (
            ClockEvent.objects.filter(
                staff__restaurant=restaurant,
                timestamp__date__gte=period_start,
                timestamp__date__lte=today,
            )
            .values("staff_id", "event_type", "location_id", "timestamp")
            .order_by("staff_id", "timestamp")
        )
    except Exception:
        logger.exception("cross-location-report: clock event query failed")
        period_clock_events = []

    # Track currently-clocked-in: walk staff_id stream, last event today
    # is IN ⇒ still clocked in at that location.
    last_event_per_staff_today: dict[Any, dict] = {}
    for ev in period_clock_events:
        loc = bucket_for(ev.get("location_id"))
        if ev.get("event_type") == "IN":
            clock_in_count_period_by_loc[loc] += 1
        if ev["timestamp"].date() == today:
            last_event_per_staff_today[ev["staff_id"]] = ev

    for ev in last_event_per_staff_today.values():
        if ev.get("event_type") == "IN":
            clocked_in_now_by_loc[bucket_for(ev.get("location_id"))] += 1

    # ── Open staff requests by location/priority ──
    # StaffRequest doesn't currently FK to BusinessLocation, but it does
    # FK to staff (CustomUser). When the user has a primary_location we
    # bucket by that, otherwise the request lives at the primary branch.
    open_qs = StaffRequest.objects.filter(
        restaurant=restaurant,
        status__in=_OPEN_STAFF_REQUEST_STATUSES,
    ).select_related("staff")
    open_by_loc_priority: dict[Any, dict[str, int]] = defaultdict(
        lambda: {"URGENT": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    )
    waiting_on_by_loc: dict[Any, int] = defaultdict(int)
    for req in open_qs.iterator(chunk_size=500):
        loc_id = None
        if req.staff and has_primary_location_fk:
            loc_id = getattr(req.staff, "primary_location_id", None)
        bucket = bucket_for(loc_id)
        priority = (req.priority or "MEDIUM").upper()
        open_by_loc_priority[bucket][priority] = (
            open_by_loc_priority[bucket].get(priority, 0) + 1
        )
        if req.status == "WAITING_ON":
            waiting_on_by_loc[bucket] += 1

    # ── Build response shape ──
    locations_payload: list[dict[str, Any]] = []
    totals = _zero_loc_metrics()
    for loc in locations:
        prio = open_by_loc_priority.get(loc.id) or {"URGENT": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        open_total = sum(prio.values())
        row = {
            "id": str(loc.id),
            "name": loc.name,
            "is_primary": bool(loc.is_primary),
            "staff_total": int(staff_count_by_loc.get(loc.id, 0)),
            "clocked_in_now": int(clocked_in_now_by_loc.get(loc.id, 0)),
            "clock_in_count_today": int(clock_in_count_period_by_loc.get(loc.id, 0)),
            "open_requests_total": int(open_total),
            "open_by_priority": {k: int(v) for k, v in prio.items()},
            "urgent_open": int(prio.get("URGENT", 0)),
            "waiting_on_count": int(waiting_on_by_loc.get(loc.id, 0)),
        }
        locations_payload.append(row)
        for k, v in row.items():
            if k in ("id", "name", "is_primary", "open_by_priority"):
                continue
            totals[k] = (totals.get(k) or 0) + (v or 0)

    # Aggregated open_by_priority mirror at totals level so chat replies
    # can quote "3 urgent across all branches" without summing again.
    totals_by_priority = {"URGENT": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for row in locations_payload:
        for k, v in row["open_by_priority"].items():
            totals_by_priority[k] = totals_by_priority.get(k, 0) + int(v)
    totals["open_by_priority"] = totals_by_priority

    payload = {
        "success": True,
        "period": period_label,
        "period_days": period_days,
        "generated_at": now.isoformat(),
        "tenant": {"id": str(restaurant.id), "name": restaurant.name},
        "locations": locations_payload,
        "totals": totals,
        "message_for_user": _format_summary_message(period_label, totals, locations_payload),
    }
    return Response(payload, status=status.HTTP_200_OK)


def _zero_loc_metrics() -> dict[str, Any]:
    return {
        "staff_total": 0,
        "clocked_in_now": 0,
        "clock_in_count_today": 0,
        "open_requests_total": 0,
        "urgent_open": 0,
        "waiting_on_count": 0,
    }
