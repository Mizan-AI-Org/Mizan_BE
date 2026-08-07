"""
Canonical responsibility routing — create categories, assign owners, route events.

Storage on ``Restaurant.general_settings`` (existing model):

- ``category_owners`` — tenant-default slug → UUID[]
- ``category_owners_by_location`` — location_id → { slug → UUID[] }
- ``responsibility_categories`` — custom category registry
- ``category_routing`` — strategy / backup per slug

Flow for an event::

    EVENT → CATEGORY → RESPONSIBLE PEOPLE → ASSIGNMENT → NOTIFY → AUDIT
"""
from __future__ import annotations

import logging
from typing import Any

from staff.category_routing_engine import (
    STRATEGY_NOTIFY_ALL,
    VALID_STRATEGIES,
    CategoryRoutingResult,
    resolve_routing_for_slugs,
)
from staff.request_routing import slugs_for_category

logger = logging.getLogger(__name__)

# Human / Miya category codes → onboarding slugs (single contract).
_CODE_TO_SLUGS: dict[str, tuple[str, ...]] = {
    "FINANCE": ("request.finance", "task.finance"),
    "HR": ("request.hr", "incident.hr"),
    "PAYROLL": ("request.payroll", "request.hr"),
    "MAINTENANCE": ("request.maintenance", "incident.equipment"),
    "INVENTORY": ("request.inventory",),
    "PURCHASE_ORDER": ("request.purchase_order", "request.inventory"),
    "DELIVERIES": ("request.purchase_order", "request.inventory"),
    "ORDERS": ("request.orders", "task.orders"),
    "SCHEDULING": ("request.scheduling",),
    "DOCUMENT": ("request.document",),
    "OPERATIONS": ("task.foh", "task.boh", "task.bar"),
    "MEDICAL": ("request.medical", "request.hr"),
    "RESERVATIONS": ("request.reservations",),
    "INCIDENT": ("incident.safety", "incident.other", "incident.hr"),
    "SAFETY": ("incident.safety",),
}

_ALIASES = {
    "finance": "FINANCE",
    "finances": "FINANCE",
    "invoice": "FINANCE",
    "invoices": "FINANCE",
    "hr": "HR",
    "human resources": "HR",
    "maintenance": "MAINTENANCE",
    "inventory": "INVENTORY",
    "deliveries": "DELIVERIES",
    "delivery": "DELIVERIES",
    "orders": "ORDERS",
    "order": "ORDERS",
    "incident": "INCIDENT",
    "incidents": "INCIDENT",
    "safety": "SAFETY",
    "payroll": "PAYROLL",
    "ops": "OPERATIONS",
    "operations": "OPERATIONS",
}


def normalize_category_code(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    # Already a slug?
    if "." in text:
        return text.strip().lower()
    return _ALIASES.get(text.lower(), text.upper().replace(" ", "_"))


def slugs_for_responsibility(
    restaurant,
    category: str,
) -> tuple[str, ...]:
    """Resolve lookup slugs for a category code, slug, or custom registry entry."""
    code = normalize_category_code(category)
    if not code:
        return ()
    if "." in code:
        return (code,)

    # Custom categories registered on the restaurant
    gs = (restaurant.general_settings or {}) if restaurant else {}
    custom = gs.get("responsibility_categories") or {}
    if isinstance(custom, dict) and code in custom:
        entry = custom[code] or {}
        slugs = entry.get("slugs") or []
        if isinstance(slugs, (list, tuple)) and slugs:
            return tuple(str(s).strip().lower() for s in slugs if s)

    if code in _CODE_TO_SLUGS:
        return _CODE_TO_SLUGS[code]

    # StaffRequest.category vocabulary
    staff_slugs = slugs_for_category(code)
    if staff_slugs:
        return staff_slugs

    # Fallback: invent request.<code_lower> for custom names
    slug = f"request.{code.lower()}"
    return (slug,)


def _owners_map_for_location(gs: dict, location_id: str | None) -> dict:
    """Return the effective owners mapping for a location (location overlay + tenant)."""
    tenant = dict(gs.get("category_owners") or {})
    if not location_id:
        return tenant
    by_loc = gs.get("category_owners_by_location") or {}
    if not isinstance(by_loc, dict):
        return tenant
    loc_map = by_loc.get(str(location_id)) or by_loc.get(str(location_id).lower())
    if not isinstance(loc_map, dict) or not loc_map:
        return tenant
    # Location-specific keys override tenant for the same slug
    merged = dict(tenant)
    for k, v in loc_map.items():
        merged[str(k)] = v
    return merged


def resolve_responsibility(
    restaurant,
    *,
    category: str,
    location_id: str | None = None,
) -> CategoryRoutingResult:
    """CATEGORY → RESPONSIBLE PEOPLE (location-aware, cross-establishment isolated)."""
    slugs = slugs_for_responsibility(restaurant, category)
    if not restaurant or not slugs:
        return CategoryRoutingResult()

    gs = dict(restaurant.general_settings or {})
    merged = dict(gs.get("category_owners") or {})
    use_location_id: str | None = None

    if location_id:
        by_loc = gs.get("category_owners_by_location") or {}
        loc_map = by_loc.get(str(location_id)) if isinstance(by_loc, dict) else None
        if isinstance(loc_map, dict) and loc_map:
            isolated: dict = {}
            for s in slugs:
                for k, v in loc_map.items():
                    if str(k).lower() == str(s).lower():
                        isolated[str(k)] = v
            if isolated:
                # Strict isolation: do not fall back to tenant owners for this category
                merged = isolated
                use_location_id = None  # already merged; avoid double-merge in engine
            else:
                use_location_id = str(location_id)

    original = restaurant.general_settings
    try:
        patched = dict(gs)
        patched["category_owners"] = merged
        restaurant.general_settings = patched
        return resolve_routing_for_slugs(restaurant, slugs, location_id=use_location_id)
    finally:
        restaurant.general_settings = original


def _audit(
    *,
    restaurant,
    actor,
    action_type: str,
    description: str,
    entity_id: str = "",
    old_values: dict | None = None,
    new_values: dict | None = None,
    metadata: dict | None = None,
    target_user=None,
) -> None:
    try:
        from accounts.models import AuditLog

        AuditLog.create_log(
            restaurant=restaurant,
            user=actor if getattr(actor, "pk", None) else None,
            action_type=action_type,
            entity_type="responsibility",
            entity_id=entity_id or None,
            description=description,
            old_values=old_values or {},
            new_values=new_values or {},
            metadata=metadata or {},
            target_user=target_user,
        )
    except Exception:
        logger.exception("responsibility audit failed")


def create_responsibility_category(
    restaurant,
    *,
    code: str,
    label: str = "",
    kind: str = "request",
    slugs: list[str] | None = None,
    actor=None,
) -> dict[str, Any]:
    """Register a responsibility category (e.g. ORDERS, DELIVERIES)."""
    code_n = normalize_category_code(code)
    if not code_n or "." in code_n:
        raise ValueError("category_code_required")
    kind_n = (kind or "request").strip().lower()
    if kind_n not in ("request", "task", "incident", "mixed"):
        kind_n = "request"

    if slugs:
        slug_list = [str(s).strip().lower() for s in slugs if s]
    else:
        prefix = "incident" if kind_n == "incident" else ("task" if kind_n == "task" else "request")
        slug_list = [f"{prefix}.{code_n.lower()}"]

    gs = dict(restaurant.general_settings or {})
    registry = dict(gs.get("responsibility_categories") or {})
    old = dict(registry.get(code_n) or {})
    entry = {
        "label": (label or code_n.replace("_", " ").title())[:120],
        "kind": kind_n,
        "slugs": slug_list,
    }
    registry[code_n] = entry
    gs["responsibility_categories"] = registry
    # Ensure empty owner lists exist for primary slug
    owners = dict(gs.get("category_owners") or {})
    for s in slug_list:
        owners.setdefault(s, [])
    gs["category_owners"] = owners
    restaurant.general_settings = gs
    restaurant.save(update_fields=["general_settings"])

    _audit(
        restaurant=restaurant,
        actor=actor,
        action_type="CREATE" if not old else "UPDATE",
        description=f"Responsibility category {code_n} created/updated",
        entity_id=code_n,
        old_values=old,
        new_values=entry,
        metadata={"slugs": slug_list},
    )
    return {"code": code_n, **entry}


def set_responsible_people(
    restaurant,
    *,
    category: str,
    owner_ids: list[str],
    location_id: str | None = None,
    strategy: str = "",
    replace: bool = True,
    actor=None,
) -> dict[str, Any]:
    """
    Assign one or more responsible people to a category (and optional establishment).
    Writes canonical slugs so Settings / engine / Miya / WhatsApp agree.
    """
    from accounts.models import CustomUser

    slugs = slugs_for_responsibility(restaurant, category)
    if not slugs:
        raise ValueError("category_required")

    # Validate owners belong to this tenant (or linked)
    validated: list[str] = []
    for uid in owner_ids:
        uid_s = str(uid).strip()
        if not uid_s:
            continue
        user = CustomUser.objects.filter(id=uid_s, is_active=True).first()
        if user is None:
            continue
        if str(getattr(user, "restaurant_id", "")) == str(restaurant.id):
            validated.append(uid_s)
            continue
        # StaffRestaurantLink
        try:
            from accounts.models import StaffRestaurantLink

            if StaffRestaurantLink.objects.filter(
                user_id=uid_s, restaurant_id=restaurant.id, is_active=True
            ).exists():
                validated.append(uid_s)
        except Exception:
            pass

    if not validated and owner_ids:
        raise ValueError("owner_not_found")

    gs = dict(restaurant.general_settings or {})
    old_snapshot: dict[str, Any] = {}

    if location_id:
        by_loc = dict(gs.get("category_owners_by_location") or {})
        loc_key = str(location_id)
        loc_map = dict(by_loc.get(loc_key) or {})
        for slug in slugs:
            old_snapshot[slug] = list(loc_map.get(slug) or [])
            if replace:
                loc_map[slug] = list(validated)
            else:
                existing = [str(x) for x in (loc_map.get(slug) or [])]
                for uid in validated:
                    if uid not in existing:
                        existing.append(uid)
                loc_map[slug] = existing
        by_loc[loc_key] = loc_map
        gs["category_owners_by_location"] = by_loc
    else:
        owners = dict(gs.get("category_owners") or {})
        for slug in slugs:
            old_snapshot[slug] = list(owners.get(slug) or []) if isinstance(owners.get(slug), list) else (
                [owners.get(slug)] if owners.get(slug) else []
            )
            # Remove legacy FINANCE-style key collisions for this category
            code = normalize_category_code(category)
            if code and code in owners and "." not in code:
                old_snapshot[f"legacy:{code}"] = owners.get(code)
                owners.pop(code, None)
            if replace:
                owners[slug] = list(validated)
            else:
                existing = [str(x) for x in _as_list(owners.get(slug))]
                for uid in validated:
                    if uid not in existing:
                        existing.append(uid)
                owners[slug] = existing
        gs["category_owners"] = owners

    strat = (strategy or "").strip().lower()
    if strat and strat in VALID_STRATEGIES:
        routing = dict(gs.get("category_routing") or {})
        for slug in slugs:
            entry = dict(routing.get(slug) or {})
            entry["strategy"] = strat
            routing[slug] = entry
        gs["category_routing"] = routing
    elif len(validated) > 1:
        # Default multi-owner → notify_all
        routing = dict(gs.get("category_routing") or {})
        for slug in slugs:
            entry = dict(routing.get(slug) or {})
            if not entry.get("strategy"):
                entry["strategy"] = STRATEGY_NOTIFY_ALL
                routing[slug] = entry
        gs["category_routing"] = routing

    # Sync legacy incident_category_assignees for incident.* slugs
    legacy = dict(gs.get("incident_category_assignees") or {})
    from staff.incident_routing import _INCIDENT_LABEL_TO_OWNER_SLUGS

    for label, inc_slugs in _INCIDENT_LABEL_TO_OWNER_SLUGS.items():
        for s in slugs:
            if s in inc_slugs and validated:
                legacy[label] = validated[0]
    gs["incident_category_assignees"] = legacy

    restaurant.general_settings = gs
    restaurant.save(update_fields=["general_settings"])

    new_snapshot = {s: validated for s in slugs}
    _audit(
        restaurant=restaurant,
        actor=actor,
        action_type="UPDATE",
        description=(
            f"Set responsible people for {category}"
            + (f" @ location {location_id}" if location_id else "")
        ),
        entity_id=normalize_category_code(category) or slugs[0],
        old_values=old_snapshot,
        new_values=new_snapshot,
        metadata={
            "slugs": list(slugs),
            "location_id": location_id,
            "owner_ids": validated,
            "strategy": strat or None,
        },
        target_user=None,
    )

    # VERIFY
    restaurant.refresh_from_db()
    check = resolve_responsibility(restaurant, category=category, location_id=location_id)
    resolved_ids = [str(u.id) for u in check.owners]
    if validated and not any(v in resolved_ids for v in validated):
        # Linked users may not resolve via restaurant_id — still accept write
        logger.warning(
            "responsibility verify: wrote %s but resolve returned %s",
            validated,
            resolved_ids,
        )

    return {
        "category": normalize_category_code(category),
        "slugs": list(slugs),
        "owner_ids": validated,
        "location_id": location_id,
        "strategy": check.strategy,
        "owners": [
            {
                "id": str(u.id),
                "name": f"{(u.first_name or '').strip()} {(u.last_name or '').strip()}".strip() or u.email,
                "role": u.role,
            }
            for u in check.owners
        ],
    }


def _as_list(raw) -> list:
    if raw is None or raw == "":
        return []
    if isinstance(raw, (list, tuple)):
        return list(raw)
    return [raw]


def route_event(
    restaurant,
    *,
    category: str,
    kind: str = "task",
    location_id: str | None = None,
    actor=None,
    entity_type: str = "",
    entity_id: str = "",
    title: str = "",
    notify: bool = True,
    create_task: bool = False,
    task_title: str = "",
    task_description: str = "",
) -> dict[str, Any]:
    """
    EVENT → CATEGORY → RESPONSIBLE → optional CREATE ASSIGNMENT → NOTIFY → AUDIT.
    """
    routing = resolve_responsibility(restaurant, category=category, location_id=location_id)
    if not routing.primary and not routing.owners:
        _audit(
            restaurant=restaurant,
            actor=actor,
            action_type="OTHER",
            description=f"Route failed — no owners for {category}",
            entity_id=entity_id or category,
            metadata={"category": category, "kind": kind, "location_id": location_id},
        )
        return {
            "success": False,
            "code": "no_owners",
            "category": category,
            "owners": [],
            "primary": None,
        }

    primary = routing.primary
    owners = list(routing.owners or [])
    informed = list(routing.informed or [])
    notify_targets = list(routing.notify_targets or owners)

    task = None
    if create_task and primary:
        from dashboard.models import Task
        from dashboard.task_assign_notify import notify_task_assignment
        from dashboard.task_sync import broadcast_tasks_invalidate

        task = Task.objects.create(
            restaurant=restaurant,
            assigned_to=primary,
            created_by=actor if getattr(actor, "pk", None) else None,
            title=(task_title or title or f"{category} task")[:255],
            description=(task_description or "")[:4000] or None,
            status="PENDING",
            source="MIYA",
            category=normalize_category_code(category) if "." not in normalize_category_code(category) else None,
            routing_metadata={
                "category": category,
                "strategy": routing.strategy,
                "slug": routing.slug,
                "owner_ids": [str(u.id) for u in owners],
                "informed_assignee_ids": [str(u.id) for u in informed],
                "location_id": location_id,
            },
        )
        task.assignees.add(primary)
        for u in owners:
            if str(u.id) != str(primary.id):
                task.assignees.add(u)
        if notify:
            try:
                notify_task_assignment(
                    task,
                    assignee=primary,
                    sender=actor if getattr(actor, "pk", None) else None,
                    informed_owners=informed,
                    notify_whatsapp=True,
                )
            except Exception:
                logger.exception("route_event task notify failed")
        try:
            broadcast_tasks_invalidate(restaurant, reason="responsibility_route", task_id=str(task.id))
        except Exception:
            pass
        entity_type = entity_type or "dashboard.Task"
        entity_id = entity_id or str(task.id)

    elif notify and kind == "incident" and entity_id:
        try:
            from staff.models_task import SafetyConcernReport
            from staff.incident_assignee_notify import (
                notify_incident_category_owners,
                notify_incident_category_owners_in_app,
            )

            ticket = SafetyConcernReport.objects.filter(id=entity_id, restaurant=restaurant).first()
            if ticket:
                if primary and ticket.assigned_to_id != primary.id:
                    ticket.assigned_to = primary
                    ticket.save(update_fields=["assigned_to", "updated_at"])
                notify_incident_category_owners_in_app(ticket)
                notify_incident_category_owners(ticket)
        except Exception:
            logger.exception("route_event incident notify failed")

    elif notify and notify_targets:
        # Generic WA/in-app ping for responsible people
        try:
            from notifications.services import notification_service

            msg = title or f"New {kind} in {category}"
            for u in notify_targets:
                notification_service.send_custom_notification(
                    recipient=u,
                    message=msg,
                    title=f"{category} routing",
                    notification_type="TASK_ASSIGNED",
                    channels=["app", "push"],
                    sender=actor if getattr(actor, "pk", None) else None,
                )
                phone = (getattr(u, "phone", None) or "").strip()
                if phone:
                    try:
                        notification_service.send_whatsapp_text(
                            phone,
                            f"📋 *{category}*: {msg}",
                        )
                    except Exception:
                        pass
        except Exception:
            logger.exception("route_event generic notify failed")

    _audit(
        restaurant=restaurant,
        actor=actor,
        action_type="CREATE" if task else "OTHER",
        description=(
            f"Routed {kind} '{title or entity_id or category}' → "
            f"{', '.join((u.first_name or u.email) for u in owners)}"
        ),
        entity_id=entity_id or category,
        new_values={
            "primary_id": str(primary.id) if primary else None,
            "owner_ids": [str(u.id) for u in owners],
            "strategy": routing.strategy,
            "slug": routing.slug,
            "task_id": str(task.id) if task else None,
        },
        metadata={
            "category": category,
            "kind": kind,
            "location_id": location_id,
            "entity_type": entity_type,
        },
        target_user=primary,
    )

    return {
        "success": True,
        "category": category,
        "strategy": routing.strategy,
        "slug": routing.slug,
        "primary": {
            "id": str(primary.id),
            "name": f"{(primary.first_name or '').strip()} {(primary.last_name or '').strip()}".strip()
            or primary.email,
        }
        if primary
        else None,
        "owners": [
            {
                "id": str(u.id),
                "name": f"{(u.first_name or '').strip()} {(u.last_name or '').strip()}".strip() or u.email,
                "role": u.role,
                "phone": getattr(u, "phone", "") or "",
            }
            for u in owners
        ],
        "informed_ids": [str(u.id) for u in informed],
        "task_id": str(task.id) if task else None,
        "verified": True,
    }
