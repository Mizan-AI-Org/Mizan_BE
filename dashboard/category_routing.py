"""
Map ``StaffRequest.category`` values to dashboard widget lanes.

Keeps ingest, search, and Miya replies aligned with the category-bucket
widgets in ``dashboard.api.category_tasks`` and inbox tabs in
``dashboard.inbox_lanes``.
"""

from __future__ import annotations

from dashboard.api.category_tasks import BUCKET_TO_CATEGORIES
from dashboard.widget_ids import (
    DASHBOARD_WIDGET_IDS,
    DEFAULT_DASHBOARD_WIDGET_ORDER,
    normalize_agent_widget_id,
)

# Primary widget bucket per staff-request category.
_CATEGORY_TO_WIDGET: dict[str, str] = {}
for _widget_id, _cats in BUCKET_TO_CATEGORIES.items():
    if _widget_id in ("urgent", "meetings", "operations"):
        continue
    for _cat in _cats:
        _CATEGORY_TO_WIDGET.setdefault(_cat, _widget_id)

# Employee wage / payslip escalations belong on Human Resources only.
_CATEGORY_TO_WIDGET["PAYROLL"] = "human_resources"

_CATEGORY_TO_WIDGET.setdefault("INVENTORY", "inventory_delivery")
_CATEGORY_TO_WIDGET.setdefault("RESERVATIONS", "reservations")
_CATEGORY_TO_WIDGET.setdefault("OTHER", "miscellaneous")

WIDGET_DISPLAY_NAMES: dict[str, str] = {
    "staff_inbox": "Staff Inbox",
    "human_resources": "Human Resources",
    "team_travel": "Team Travel",
    "team_medical_service": "Team Medical Service",
    "finance": "Finance",
    "maintenance": "Maintenance",
    "purchase_orders": "Purchase Orders",
    "operations_tasks": "Operations Tasks",
    "miscellaneous": "Miscellaneous",
    "inventory_delivery": "Inventory & Delivery",
    "reservations": "Reservations",
    "incidents": "Reported Incidents",
    "urgent_top": "Urgent",
}


def primary_widget_for_category(category: str | None) -> str:
    cat = (category or "OTHER").upper().strip()
    return _CATEGORY_TO_WIDGET.get(cat, "miscellaneous")


def dashboard_widgets_for_category(category: str | None) -> list[str]:
    """Built-in widgets to pin when a request lands in ``category``."""
    primary = primary_widget_for_category(category)
    widgets: list[str] = [primary]
    return [w for w in widgets if w in DASHBOARD_WIDGET_IDS]


def categories_with_dedicated_lane() -> frozenset[str]:
    """StaffRequest categories owned by a named operational dashboard lane."""
    cats: set[str] = set()
    for bucket, bucket_cats in BUCKET_TO_CATEGORIES.items():
        if bucket in ("urgent", "meetings", "operations"):
            continue
        cats.update(bucket_cats)
    cats.update(_CATEGORY_TO_WIDGET.keys())
    return frozenset(cats)


def dashboard_widgets_for_incident() -> list[str]:
    return [w for w in ("incidents", "staff_inbox") if w in DASHBOARD_WIDGET_IDS]


def widget_lane_label(widget_id: str) -> str:
    return WIDGET_DISPLAY_NAMES.get(widget_id, widget_id.replace("_", " ").title())


def category_lane_hint(category: str | None) -> str:
    widget_id = primary_widget_for_category(category)
    label = widget_lane_label(widget_id)
    return f"{label} widget (?lane={widget_id})"


def ensure_dashboard_widgets_for_managers(
    restaurant,
    *,
    category: str | None = None,
    incident: bool = False,
) -> dict:
    """
    Best-effort: add the relevant lane widget to every manager/admin/owner
    layout for this tenant so incoming requests surface immediately without
    a manual "Add widget" step.
    """
    from accounts.models import CustomUser
    from dashboard.views_widget_layout import _can_customize_dashboard, _clean_order

    widget_ids = (
        dashboard_widgets_for_incident()
        if incident
        else dashboard_widgets_for_category(category)
    )
    widget_ids = [
        w
        for w in dict.fromkeys(normalize_agent_widget_id(x) or x for x in widget_ids)
        if w in DASHBOARD_WIDGET_IDS
    ]

    managers = CustomUser.objects.filter(
        restaurant=restaurant,
        is_active=True,
        role__in=["MANAGER", "ADMIN", "OWNER"],
    )
    updated: list[dict] = []
    for user in managers:
        if not _can_customize_dashboard(user):
            continue
        current = _clean_order(getattr(user, "dashboard_widget_order", None), user)
        if current is None:
            current = list(DEFAULT_DASHBOARD_WIDGET_ORDER)
        added: list[str] = []
        for wid in widget_ids:
            if wid not in current:
                current.append(wid)
                added.append(wid)
        if added:
            user.dashboard_widget_order = current
            user.save(update_fields=["dashboard_widget_order"])
            updated.append({"user_id": str(user.id), "added": added})

    return {
        "widgets": widget_ids,
        "primary_widget": widget_ids[-1] if len(widget_ids) > 1 else widget_ids[0],
        "managers_updated": updated,
    }
