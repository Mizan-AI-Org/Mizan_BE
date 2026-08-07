"""Category owners / responsibility — thin ops wrapper over staff.responsibility."""
from __future__ import annotations

from miya.services.ops.context import OpsContext, require_permission, require_restaurant
from miya.services.ops.result import OpsResult, clarify, fail, ok


def find_category_owners(
    ctx: OpsContext,
    *,
    category: str = "",
    q: str = "",
    location_id: str = "",
) -> OpsResult:
    err = require_restaurant(ctx) or require_permission(ctx, "miya_full_tools")
    if err:
        return err

    raw = (category or q or "").strip()
    if not raw:
        return fail(code="category_required", message="Which category? (finance, HR, maintenance, …)")

    from staff.responsibility import normalize_category_code, resolve_responsibility

    loc = (location_id or ctx.location_id or "").strip() or None
    routing = resolve_responsibility(ctx.restaurant, category=raw, location_id=loc)
    owners = []
    for u in routing.owners or ([routing.primary] if routing.primary else []):
        if not u:
            continue
        owners.append(
            {
                "id": str(u.id),
                "name": f"{(u.first_name or '').strip()} {(u.last_name or '').strip()}".strip() or u.email,
                "role": u.role,
                "phone": getattr(u, "phone", "") or "",
            }
        )

    cat = normalize_category_code(raw)
    if not owners:
        return fail(
            code="no_category_owner",
            message=(
                f"No one is configured as owner for {cat}. "
                "Add them in Settings → Who owns what."
            ),
            data={"category": cat, "owners": [], "location_id": loc},
        )

    names = ", ".join(o["name"] for o in owners)
    return ok(
        message=f"*{cat}* is owned by: {names} (strategy: {routing.strategy}).",
        verified=True,
        data={
            "category": cat,
            "owners": owners,
            "strategy": routing.strategy,
            "slug": routing.slug,
            "location_id": loc,
        },
    )


def create_category(
    ctx: OpsContext,
    *,
    code: str = "",
    label: str = "",
    kind: str = "request",
    slugs: list | None = None,
) -> OpsResult:
    err = require_restaurant(ctx) or require_permission(ctx, "manage_settings")
    if err:
        return err
    if not (code or "").strip():
        return clarify(message="What should I call this category? (e.g. ORDERS, DELIVERIES)")

    from staff.responsibility import create_responsibility_category

    try:
        entry = create_responsibility_category(
            ctx.restaurant,
            code=code,
            label=label,
            kind=kind,
            slugs=list(slugs) if slugs else None,
            actor=ctx.user,
        )
    except ValueError as exc:
        return fail(code=str(exc), message=f"Couldn't create category: {exc}")

    return ok(
        message=f"Created responsibility category *{entry['code']}* ({entry.get('label')}).",
        verified=True,
        data=entry,
    )


def assign_responsibility(
    ctx: OpsContext,
    *,
    category: str = "",
    owner_name: str = "",
    owner_id: str = "",
    owner_ids: list | None = None,
    owner_names: list | None = None,
    location_id: str = "",
    strategy: str = "",
    replace: bool = True,
) -> OpsResult:
    """Assign one or more responsible people (canonical slugs + audit)."""
    err = require_restaurant(ctx) or require_permission(ctx, "manage_settings")
    if err:
        return err

    if not (category or "").strip():
        return clarify(message="Which category should I assign responsibility for? (finance, HR, …)")

    from dashboard.views_agent import _resolve_assignee
    from staff.responsibility import set_responsible_people

    ids: list[str] = []
    if owner_ids:
        ids.extend(str(x) for x in owner_ids if x)
    if owner_id:
        ids.append(str(owner_id).strip())

    names = list(owner_names or [])
    if owner_name:
        names.append(owner_name)

    for name in names:
        assignee, aerr = _resolve_assignee(
            {"assignee_name": name, "name": name},
            ctx.restaurant,
        )
        if aerr or not assignee:
            return fail(
                code="owner_not_found",
                message=aerr or f"I couldn't find staff matching '{name}'.",
            )
        ids.append(str(assignee.id))

    # de-dupe
    seen: set[str] = set()
    unique_ids: list[str] = []
    for i in ids:
        if i and i not in seen:
            seen.add(i)
            unique_ids.append(i)

    if not unique_ids:
        return clarify(message="Who should be responsible? Give me one or more staff names.")

    loc = (location_id or ctx.location_id or "").strip() or None
    try:
        result = set_responsible_people(
            ctx.restaurant,
            category=category,
            owner_ids=unique_ids,
            location_id=loc,
            strategy=strategy,
            replace=replace,
            actor=ctx.user,
        )
    except ValueError as exc:
        return fail(code=str(exc), message=str(exc))

    names_out = ", ".join(o["name"] for o in result.get("owners") or [])
    where = f" at this establishment" if loc else ""
    return ok(
        message=f"{names_out or 'Owners'} now responsible for *{result.get('category')}*{where}.",
        verified=True,
        data=result,
    )


def route_responsibility_event(
    ctx: OpsContext,
    *,
    category: str = "",
    kind: str = "task",
    title: str = "",
    create_task: bool = False,
    task_description: str = "",
    entity_id: str = "",
    location_id: str = "",
    notify: bool = True,
) -> OpsResult:
    err = require_restaurant(ctx) or require_permission(ctx, "manage_widgets")
    if err:
        return err
    if not (category or "").strip():
        return clarify(message="Which category should I route this to?")

    from staff.responsibility import route_event

    loc = (location_id or ctx.location_id or "").strip() or None
    result = route_event(
        ctx.restaurant,
        category=category,
        kind=kind,
        location_id=loc,
        actor=ctx.user,
        entity_id=entity_id,
        title=title,
        notify=notify,
        create_task=create_task,
        task_title=title,
        task_description=task_description,
    )
    if not result.get("success"):
        return fail(
            code=result.get("code") or "route_failed",
            message=f"Couldn't route — no owners for {category}.",
            data=result,
        )
    primary = (result.get("primary") or {}).get("name") or "owners"
    return ok(
        message=f"Routed to {primary} ({result.get('strategy')}).",
        verified=bool(result.get("verified")),
        data=result,
    )
