"""
Canonical scoping for clock-in / clock-out data.

Why this module exists
----------------------
Before this helper, every endpoint that listed today's attendance,
clock-ins or live presence wrote its own filter:

    ClockEvent.objects.filter(staff__restaurant=request.user.restaurant, ...)

That filter only matches clock-ins where the staff member's *primary*
restaurant FK equals the manager's restaurant. In production we have:

* **Multi-site chains** — a clock-in carries a ``BusinessLocation``
  pointing at the actual branch. The branch knows which restaurant it
  belongs to. That's the strongest signal of "this clock-in belongs to
  restaurant R" but the old filter ignored it entirely.
* **Multi-restaurant staff** — a staff member can be linked to several
  restaurants via :class:`accounts.StaffRestaurantLink`. Only one of
  them is the primary. The old filter dropped clock-ins from staff
  whose primary restaurant differed from the manager's view.
* **Imported / legacy users** — bulk imports occasionally land users
  with ``restaurant_id = NULL`` while their clock-ins ride at a real
  branch. The old filter returned an empty set in that case.

Net effect: the Clock-Ins widget and "Présence en direct" both read
zero on dashboards that *should* have shown a full floor. This helper
fixes the predicate in one place so every endpoint inherits the
correct behaviour.

The helper also exposes :func:`restaurant_ids_for_clock_event` so the
post_save signal can bust the dashboard cache for *every* restaurant
the event belongs to (location's restaurant + staff's primary +
active secondary links), not just the staff's primary.
"""

from __future__ import annotations

from typing import Iterable

from django.db.models import Q, QuerySet

from .models import ClockEvent


def clock_events_for_restaurant_qs(
    restaurant,
    *,
    event_type: str | Iterable[str] | None = None,
    date=None,
) -> QuerySet[ClockEvent]:
    """Return today's (or any-date's) ClockEvents that belong to ``restaurant``.

    A clock event belongs to a restaurant when ANY of:

    1. ``event.location.restaurant == restaurant`` — strongest signal,
       the event was recorded *on* one of this restaurant's branches.
    2. The event has no location (legacy / single-site rows) AND the
       staff's primary restaurant is this restaurant.
    3. The event has no location AND the staff is linked to this
       restaurant via an active :class:`accounts.StaffRestaurantLink`.

    The branches are unioned so a clock-in still appears in the
    dashboard of a multi-restaurant employee's *current* venue even if
    their primary restaurant FK points elsewhere.
    """
    if not restaurant:
        return ClockEvent.objects.none()

    rid = getattr(restaurant, "id", restaurant)

    qs = ClockEvent.objects.filter(
        Q(location__restaurant_id=rid)
        | Q(location__isnull=True, staff__restaurant_id=rid)
        | Q(
            location__isnull=True,
            staff__restaurant_links__restaurant_id=rid,
            staff__restaurant_links__is_active=True,
        )
    ).distinct()

    if event_type is not None:
        if isinstance(event_type, str):
            qs = qs.filter(event_type=event_type)
        else:
            qs = qs.filter(event_type__in=list(event_type))

    if date is not None:
        qs = qs.filter(timestamp__date=date)

    return qs


def restaurant_ids_for_clock_event(event: ClockEvent) -> set:
    """Restaurant ids that should treat ``event`` as theirs.

    Used by the post_save signal so cache invalidation matches the
    list-side predicate above. If we only invalidated the staff's
    primary restaurant, the dashboard for the venue where the event
    actually happened would keep serving a stale "0 clock-ins" payload
    until the natural TTL expires.
    """
    rids: set = set()

    loc = getattr(event, "location", None)
    if loc is not None:
        loc_rid = getattr(loc, "restaurant_id", None)
        if loc_rid:
            rids.add(loc_rid)

    staff = getattr(event, "staff", None)
    if staff is not None:
        primary_rid = getattr(staff, "restaurant_id", None)
        if primary_rid:
            rids.add(primary_rid)

        # Best-effort fan-out to active secondary links. Wrapped in
        # try/except so the signal can never break a clock-in commit
        # (e.g. on environments where the link table doesn't exist
        # yet because a migration is pending).
        try:
            from accounts.models import StaffRestaurantLink

            extra = StaffRestaurantLink.objects.filter(
                user_id=staff.id, is_active=True
            ).values_list("restaurant_id", flat=True)
            for link_rid in extra:
                if link_rid:
                    rids.add(link_rid)
        except Exception:
            pass

    return rids
