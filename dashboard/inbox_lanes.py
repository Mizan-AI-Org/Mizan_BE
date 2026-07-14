"""
Dashboard widget → staff-request inbox tab registry.

Inbox category tabs on ``/dashboard/staff-requests`` are **not** a fixed
list for every tenant. They appear only when the manager has the matching
operational widget on their dashboard (added by Miya or the UI).

Each built-in command-centre widget maps to one inbox lane (label +
``StaffRequest.category`` filter). When Miya adds ``team_medical_service``
to the layout, the "Team Medical Service" tab is created automatically;
when the widget is removed, the tab disappears on the next load.

Keep in sync with ``dashboard.api.category_tasks.BUCKET_TO_CATEGORIES``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from accounts.models import CustomUser


@dataclass(frozen=True)
class InboxLaneDef:
    """One filter tab on the All Requests command centre."""

    lane_id: str
    widget_id: str
    label: str
    page_title: str
    page_subtitle: str
    categories: tuple[str, ...]
    icon: str


# Widget ids that surface an inbox tab when present in the user's layout.
WIDGET_INBOX_LANES: dict[str, InboxLaneDef] = {
    "team_travel": InboxLaneDef(
        lane_id="team_travel",
        widget_id="team_travel",
        label="Team Travel",
        page_title="Team Travel",
        page_subtitle=(
            "Leave, travel, and scheduling requests — review, assign, approve, and close from here."
        ),
        categories=("SCHEDULING",),
        icon="calendar",
    ),
    "team_medical_service": InboxLaneDef(
        lane_id="team_medical_service",
        widget_id="team_medical_service",
        label="Team Medical Service",
        page_title="Team Medical Service",
        page_subtitle=(
            "Medical and occupational-health requests — review, assign, approve, and close from here."
        ),
        categories=("MEDICAL",),
        icon="heart",
    ),
    "human_resources": InboxLaneDef(
        lane_id="human_resources",
        widget_id="human_resources",
        label="Human Resources",
        page_title="Human Resources",
        page_subtitle="HR, documents, and payroll asks — contracts, IDs, onboarding, and unpaid wages.",
        categories=("HR", "DOCUMENT", "PAYROLL"),
        icon="briefcase",
    ),
    "finance": InboxLaneDef(
        lane_id="finance",
        widget_id="finance",
        label="Finance",
        page_title="Finance",
        page_subtitle="Invoices, bills, and money-out requests.",
        categories=("FINANCE",),
        icon="wallet",
    ),
    "maintenance": InboxLaneDef(
        lane_id="maintenance",
        widget_id="maintenance",
        label="Maintenance",
        page_title="Maintenance",
        page_subtitle="Repairs, equipment, and facility maintenance requests.",
        categories=("MAINTENANCE",),
        icon="wrench",
    ),
    "operations_tasks": InboxLaneDef(
        lane_id="operations_tasks",
        widget_id="operations_tasks",
        label="Operations",
        page_title="Operations",
        page_subtitle="Day-to-day operational follow-ups and process tasks.",
        categories=("OPERATIONS",),
        icon="briefcase",
    ),
    "purchase_orders": InboxLaneDef(
        lane_id="purchase_orders",
        widget_id="purchase_orders",
        label="Purchases",
        page_title="Purchases",
        page_subtitle="Procurement and purchase-order requests.",
        categories=("PURCHASE_ORDER",),
        icon="shopping-bag",
    ),
    "miscellaneous": InboxLaneDef(
        lane_id="miscellaneous",
        widget_id="miscellaneous",
        label="Miscellaneous",
        page_title="Miscellaneous",
        page_subtitle="General requests that did not match a named operational lane.",
        categories=("OTHER",),
        icon="layers",
    ),
    "reservations": InboxLaneDef(
        lane_id="reservations",
        widget_id="reservations",
        label="Reservations",
        page_title="Reservations",
        page_subtitle="Bookings, table holds, and guest-list requests.",
        categories=("RESERVATIONS",),
        icon="book-open",
    ),
    "inventory_delivery": InboxLaneDef(
        lane_id="inventory_delivery",
        widget_id="inventory_delivery",
        label="Inventory",
        page_title="Inventory",
        page_subtitle="Stock levels, deliveries, and inventory observations.",
        categories=("INVENTORY",),
        icon="package",
    ),
}


def _serialize_lane(defn: InboxLaneDef) -> dict[str, Any]:
    return {
        "lane_id": defn.lane_id,
        "widget_id": defn.widget_id,
        "label": defn.label,
        "page_title": defn.page_title,
        "page_subtitle": defn.page_subtitle,
        "categories": list(defn.categories),
        "icon": defn.icon,
    }


def inbox_lanes_for_widget_order(widget_order: list[str] | None) -> list[dict[str, Any]]:
    """Return inbox lane payloads in dashboard widget order (deduped)."""
    order = widget_order or []
    lanes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for widget_id in order:
        defn = WIDGET_INBOX_LANES.get(widget_id)
        if defn is None or defn.lane_id in seen:
            continue
        seen.add(defn.lane_id)
        lanes.append(_serialize_lane(defn))
    return lanes


def inbox_lanes_for_user(user: CustomUser) -> list[dict[str, Any]]:
    from .views_widget_layout import _clean_order
    from .widget_ids import DEFAULT_DASHBOARD_WIDGET_ORDER

    order = _clean_order(getattr(user, "dashboard_widget_order", None), user)
    if order is None:
        order = list(DEFAULT_DASHBOARD_WIDGET_ORDER)
    return inbox_lanes_for_widget_order(order)


def resolve_lane_id(
    *,
    lane_id: str | None = None,
    categories: list[str] | None = None,
    enabled_lanes: list[dict[str, Any]] | None = None,
) -> str | None:
    """Map ``?lane=`` or ``?category=`` deep-link params to an enabled lane id."""
    lanes = enabled_lanes or []
    by_id = {lane["lane_id"]: lane for lane in lanes}

    if lane_id:
        lid = str(lane_id).strip()
        return lid if lid in by_id else None

    if not categories:
        return None

    cats = tuple(sorted({str(c).upper().strip() for c in categories if c}))
    if not cats:
        return None

    for lane in lanes:
        lane_cats = tuple(sorted(lane.get("categories") or []))
        if lane_cats == cats:
            return lane["lane_id"]
    # Single-category fallback: first lane that includes this category.
    if len(cats) == 1:
        for lane in lanes:
            if cats[0] in (lane.get("categories") or []):
                return lane["lane_id"]
    return None
