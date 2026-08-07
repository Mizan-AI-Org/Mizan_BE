"""Find / select / switch establishments (BusinessLocation branches)."""
from __future__ import annotations

from miya.services.ops.context import (
    OpsContext,
    assert_location_access,
    require_restaurant,
)
from miya.services.ops.result import OpsResult, clarify, fail, ok
from miya.services.ops.scoping import (
    resolve_location_by_name,
    serialize_location,
    visible_locations_for_user,
)


def find_establishments(ctx: OpsContext, *, q: str = "", name: str = "") -> OpsResult:
    err = require_restaurant(ctx)
    if err:
        return err

    visible = visible_locations_for_user(ctx.user, ctx.restaurant)
    needle = (q or name or "").strip()

    if not visible:
        tenant = {
            "id": str(ctx.restaurant.id),
            "name": ctx.restaurant.name,
            "is_primary": True,
            "kind": "restaurant",
        }
        return ok(
            message=f"Workspace *{ctx.restaurant.name}* (no separate branches configured).",
            verified=True,
            data={
                "establishments": [tenant],
                "count": 1,
                "restaurant": tenant,
                "active_location_id": ctx.location_id,
            },
        )

    if needle:
        match, matches = resolve_location_by_name(ctx.restaurant, needle, visible=visible)
        if match:
            row = serialize_location(match)
            return ok(
                message=f"Found establishment: {row['name']}.",
                verified=True,
                data={
                    "establishments": [row],
                    "count": 1,
                    "active_location_id": ctx.location_id,
                    "matched_id": row["id"],
                },
                miya_directive=(
                    "If the user wants to switch context, call set_establishment_context "
                    f"with location_id={row['id']}."
                ),
            )
        if len(matches) > 1:
            rows = [serialize_location(L) for L in matches]
            return clarify(
                message="Several establishments match — which one?",
                data={"establishments": rows, "count": len(rows), "needs_establishment": True},
            )
        return fail(
            code="establishment_not_found",
            message=f"I couldn't find an establishment matching '{needle}' that you can access.",
            data={"establishments": [serialize_location(L) for L in visible]},
        )

    rows = [serialize_location(L) for L in visible]
    active = next((r for r in rows if r["id"] == ctx.location_id), None)
    msg = f"You have access to {len(rows)} establishment(s)."
    if active:
        msg = f"Current context: {active['name']}. " + msg
    return ok(
        message=msg,
        verified=True,
        data={
            "establishments": rows,
            "count": len(rows),
            "active_location_id": ctx.location_id,
            "active_location_name": ctx.location_name,
        },
    )


def set_establishment_context(
    ctx: OpsContext,
    *,
    location_id: str = "",
    q: str = "",
    name: str = "",
) -> OpsResult:
    """
    Switch sticky establishment context (Miya / WhatsApp / dashboard session).
    Example: user says 'What about Casablanca?' → set_establishment_context(q='Casablanca').
    """
    err = require_restaurant(ctx)
    if err:
        return err

    visible = visible_locations_for_user(ctx.user, ctx.restaurant)
    lid = (location_id or "").strip()
    needle = (q or name or "").strip()

    loc = None
    if lid:
        denied = assert_location_access(ctx, lid)
        if denied:
            return denied
        loc = next((L for L in visible if str(L.id) == lid), None)
        if not loc:
            return fail(
                code="location_forbidden",
                message="You don't have access to that establishment.",
            )
    elif needle:
        loc, matches = resolve_location_by_name(ctx.restaurant, needle, visible=visible)
        if not loc and len(matches) > 1:
            return clarify(
                message="Several establishments match — which one?",
                data={
                    "establishments": [serialize_location(L) for L in matches],
                    "needs_establishment": True,
                },
            )
        if not loc:
            return fail(
                code="establishment_not_found",
                message=f"I couldn't find '{needle}' among your establishments.",
                data={"establishments": [serialize_location(L) for L in visible]},
            )
    else:
        return clarify(
            message="Which establishment should I switch to?",
            data={
                "establishments": [serialize_location(L) for L in visible],
                "needs_establishment": True,
            },
        )

    # Mutate context for this turn (caller also persists to session)
    ctx.location_id = str(loc.id)
    ctx.location_name = loc.name
    row = serialize_location(loc)

    # Persist to WhatsApp session when available
    if ctx.channel == "whatsapp":
        try:
            _persist_whatsapp_location(ctx, loc)
        except Exception:
            pass

    try:
        from miya.services.intelligence.working_memory import update_working_memory

        update_working_memory(
            user=ctx.user,
            restaurant=ctx.restaurant,
            establishment_id=row["id"],
            establishment_name=row["name"],
        )
    except Exception:
        pass

    return ok(
        message=f"Switched context to {loc.name}. I'll use this establishment until you switch again.",
        verified=True,
        data={
            "establishment": row,
            "location_id": row["id"],
            "location_name": row["name"],
            "active_location_id": row["id"],
            "session_patch": {
                "location_id": row["id"],
                "location_name": row["name"],
            },
        },
        miya_directive=(
            "Confirm the switch briefly. For follow-up questions in this turn, "
            "use this location_id. Do not mix data from other establishments."
        ),
    )


def _persist_whatsapp_location(ctx: OpsContext, loc) -> None:
    from notifications.models import WhatsAppSession

    phone = "".join(filter(str.isdigit, str(getattr(ctx.user, "phone", None) or "")))
    if len(phone) < 6:
        return
    session = WhatsAppSession.objects.filter(phone=phone).first()
    if not session:
        return
    context = dict(session.context or {}) if isinstance(session.context, dict) else {}
    context["location_id"] = str(loc.id)
    context["location_name"] = loc.name
    session.context = context
    session.save(update_fields=["context", "updated_at"] if hasattr(session, "updated_at") else ["context"])
