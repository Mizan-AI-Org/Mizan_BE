"""Find / create / route incidents — SafetyConcernReport is the canonical store."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.db.models import Q
from django.utils import timezone

from miya.services.ops.context import OpsContext, require_permission, require_restaurant
from miya.services.ops.result import OpsResult, fail, ok


def _serialize_concern(row, *, detail: bool = False) -> dict[str, Any]:
    from staff.incident_evidence import incident_has_photo_evidence, list_incident_photos

    assignee = getattr(row, "assigned_to", None)
    aname = ""
    if assignee:
        aname = f"{(assignee.first_name or '').strip()} {(assignee.last_name or '').strip()}".strip() or assignee.email
    photos = list_incident_photos(row) if detail else []
    has_photo = incident_has_photo_evidence(row)
    evidence = getattr(row, "photo_evidence", None) or []
    if detail:
        photo_count = len(photos)
    elif evidence:
        photo_count = len([e for e in evidence if isinstance(e, dict)])
    else:
        photo_count = 1 if has_photo else 0
    payload = {
        "id": str(row.id),
        "title": getattr(row, "title", None) or (getattr(row, "description", "") or "")[:80],
        "status": row.status,
        "incident_type": getattr(row, "incident_type", None) or getattr(row, "category", None) or "",
        "severity": getattr(row, "severity", None) or getattr(row, "priority", None) or "",
        "assignee_name": aname or None,
        "assignee_id": str(assignee.id) if assignee else None,
        "created_at": row.created_at.isoformat() if getattr(row, "created_at", None) else None,
        "has_photo": has_photo,
        "photo_count": photo_count,
        "source": "safety_concern",
    }
    if detail:
        payload["description"] = getattr(row, "description", None) or ""
        payload["location"] = getattr(row, "location", None) or ""
        payload["photos"] = photos
        payload["photo_urls"] = [p["url"] for p in photos if p.get("url")]
        payload["attachments"] = [
            {
                "url": p["url"],
                "name": p.get("filename") or "photo.jpg",
                "content_type": p.get("mime_type") or "image/jpeg",
            }
            for p in photos
            if p.get("url")
        ]
        if getattr(row, "resolved_at", None):
            payload["resolved_at"] = row.resolved_at.isoformat()
        if getattr(row, "resolution_notes", None):
            payload["resolution_notes"] = row.resolution_notes
    return payload


def find_incidents(
    ctx: OpsContext,
    *,
    q: str = "",
    status: str = "",
    since: str = "",
    days: int | None = None,
    limit: int = 20,
) -> OpsResult:
    err = require_restaurant(ctx)
    if err:
        return err
    err = require_permission(ctx, None)
    if err:
        return err

    from miya.services.ops.context import require_establishment_context
    from miya.services.ops.scoping import apply_location_scope, filter_visible_location_ids

    est_err = require_establishment_context(ctx, for_action="incidents")
    if est_err:
        return est_err

    from staff.models_task import SafetyConcernReport

    qs = SafetyConcernReport.objects.filter(restaurant=ctx.restaurant).select_related(
        "assigned_to", "reporter"
    )
    if ctx.location_id:
        qs = apply_location_scope(qs, location_id=ctx.location_id, field="business_location_id")
    elif len(ctx.available_locations) > 1:
        qs = filter_visible_location_ids(
            qs,
            location_ids=[r["id"] for r in ctx.available_locations],
            field="business_location_id",
        )

    raw_status = (status or "").strip().upper()
    # Keyword lookup searches all statuses (matches agent_list_incidents).
    if q and not raw_status:
        raw_status = "ALL"
    if raw_status in ("OPEN", "ACTIVE", ""):
        qs = qs.filter(status="OPEN")
    elif raw_status not in ("ALL", "*"):
        qs = qs.filter(status__iexact=raw_status)

    if days is not None:
        qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=max(0, int(days))))
    elif (since or "").lower() in ("yesterday", "hier"):
        start = timezone.localdate() - timedelta(days=1)
        qs = qs.filter(created_at__date=start)
    elif (since or "").lower() in ("today", "aujourd'hui", "aujourdhui"):
        qs = qs.filter(created_at__date=timezone.localdate())

    if q:
        qs = qs.filter(
            Q(title__icontains=q)
            | Q(description__icontains=q)
            | Q(incident_type__icontains=q)
        )

    rows = [_serialize_concern(r) for r in qs.order_by("-created_at")[: max(1, min(limit, 40))]]
    if not rows:
        where = f" at {ctx.location_name}" if ctx.location_name else ""
        return fail(
            code="incidents_not_found",
            message=f"No incidents match that filter{where}.",
            data={"incidents": [], "count": 0},
        )
    where = f" at {ctx.location_name}" if ctx.location_name else ""
    return ok(
        message=f"Found {len(rows)} incident(s){where}.",
        verified=True,
        data={"incidents": rows, "count": len(rows), "location_id": ctx.location_id, "location_name": ctx.location_name},
    )


def find_incident_responsible(ctx: OpsContext, *, incident_type: str = "", q: str = "") -> OpsResult:
    err = require_restaurant(ctx) or require_permission(ctx, "manage_widgets")
    if err:
        return err

    from staff.incident_routing import resolve_default_assignee_for_incident_type
    from staff.category_routing_engine import resolve_routing_for_incident_type

    label = (incident_type or q or "General").strip()
    routing = resolve_routing_for_incident_type(ctx.restaurant, label)
    owners = []
    for u in routing.owners or ([routing.primary] if routing.primary else []):
        if not u:
            continue
        owners.append(
            {
                "id": str(u.id),
                "name": f"{(u.first_name or '').strip()} {(u.last_name or '').strip()}".strip() or u.email,
                "role": u.role,
            }
        )
    if not owners:
        primary = resolve_default_assignee_for_incident_type(ctx.restaurant, label)
        if primary:
            owners = [
                {
                    "id": str(primary.id),
                    "name": f"{(primary.first_name or '').strip()} {(primary.last_name or '').strip()}".strip()
                    or primary.email,
                    "role": primary.role,
                }
            ]
    if not owners:
        return fail(
            code="no_owner",
            message=(
                f"No one is configured to receive '{label}' incidents. "
                "Set owners in Settings → Who owns what."
            ),
        )
    names = ", ".join(o["name"] for o in owners)
    return ok(
        message=f"For '{label}' incidents, responsible: {names}.",
        verified=True,
        data={"incident_type": label, "owners": owners, "strategy": routing.strategy},
    )


def create_incident(
    ctx: OpsContext,
    *,
    description: str,
    incident_type: str | None = None,
    severity: str | None = None,
    occurred_at=None,
    shift=None,
    audio_evidence=None,
    title: str = "",
) -> OpsResult:
    """
    Create a SafetyConcernReport (canonical incident store for dashboard + WhatsApp).
    Routes to category owners via incident_routing.
    """
    err = require_restaurant(ctx)
    if err:
        return err

    desc = (description or "").strip()
    if not desc:
        return fail(code="description_required", message="I need a description of the incident.")

    from miya.services.ops.context import require_establishment_context

    est_err = require_establishment_context(ctx, for_action="reporting an incident")
    if est_err:
        return est_err

    from staff.models_task import SafetyConcernReport
    from staff.incident_routing import (
        normalize_incident_category_for_storage,
        resolve_default_assignee_for_incident_type,
    )

    loc = None
    sev = severity
    try:
        from notifications.utils import extract_incident_location, infer_severity

        loc = extract_incident_location(desc) or None
        if not sev:
            sev = infer_severity(desc)
    except Exception:
        sev = sev or "MEDIUM"

    itype = normalize_incident_category_for_storage(incident_type or "General")
    when = occurred_at or timezone.now()
    assignee = resolve_default_assignee_for_incident_type(ctx.restaurant, itype)

    ticket = SafetyConcernReport.objects.create(
        restaurant=ctx.restaurant,
        reporter=ctx.user if getattr(ctx.user, "pk", None) else None,
        is_anonymous=False,
        incident_type=itype,
        title=(title or f"{itype} incident")[:255],
        description=desc,
        location=loc,
        business_location_id=ctx.location_id or None,
        severity=sev or "MEDIUM",
        status="OPEN",
        occurred_at=when,
        shift=shift,
        assigned_to=assignee,
        audio_evidence=audio_evidence or [],
    )

    fresh = SafetyConcernReport.objects.filter(id=ticket.id, restaurant=ctx.restaurant).first()
    if not fresh or fresh.status != "OPEN":
        return fail(
            code="verify_failed",
            message="I tried to log the incident but couldn't verify it was saved.",
        )

    row = _serialize_concern(fresh)

    try:
        from core.operational_audit.service import INCIDENT_CREATED, record_operational_audit_event

        record_operational_audit_event(
            restaurant=ctx.restaurant,
            event_type=INCIDENT_CREATED,
            entity_type="incident",
            entity_id=str(fresh.id),
            entity_label=row.get("title") or desc[:80],
            actor=ctx.user,
            location_id=ctx.location_id or "",
            channel=ctx.channel or "dashboard",
            operation_id=f"incident:create:{fresh.id}",
            new_state={"status": fresh.status, "incident_type": itype},
            summary=f"Incident created: {row.get('title') or desc[:80]}",
        )
    except Exception:
        pass

    # EVENT → CATEGORY → RESPONSIBLE → NOTIFY → AUDIT (shared with dashboard/WA)
    try:
        from staff.responsibility import route_event

        route_event(
            ctx.restaurant,
            category=itype,
            kind="incident",
            location_id=ctx.location_id,
            actor=ctx.user,
            entity_type="SafetyConcernReport",
            entity_id=str(fresh.id),
            title=row.get("title") or desc[:80],
            notify=False,  # SafetyConcern signals already WhatsApp owners
            create_task=False,
        )
    except Exception:
        pass

    # WhatsApp: next inbound image attaches as evidence
    if ctx.channel == "whatsapp":
        try:
            phone = (getattr(ctx.user, "phone", None) or "").strip()
            if phone:
                from staff.incident_evidence import arm_whatsapp_incident_photo_await

                arm_whatsapp_incident_photo_await(
                    phone=phone, user=ctx.user, ticket_id=str(fresh.id)
                )
        except Exception:
            pass

    return ok(
        message=(
            f"Incident logged ({row['id'][:8]}) — {row['incident_type']}"
            + (f", routed to {row['assignee_name']}" if row.get("assignee_name") else "")
            + "."
        ),
        verified=True,
        data={"incident": row, "incidents": [row], "ticket_id": str(fresh.id)},
    )


def attach_incident_photo(
    ctx: OpsContext,
    *,
    incident_id: str,
    document_id: str = "",
    file_bytes: bytes | None = None,
    mime_type: str = "image/jpeg",
    filename: str = "",
    caption: str = "",
    media_id: str = "",
) -> OpsResult:
    """
    Attach photo evidence from a TenantDocument id and/or raw bytes.
    Same-turn dashboard/voice path uses document_id from multimodal upload.
    """
    err = require_restaurant(ctx)
    if err:
        return err
    if not incident_id:
        return fail(code="incident_required", message="I need the incident to attach a photo.")

    data = file_bytes
    mime = mime_type
    name = filename
    if document_id and not data:
        from miya.services.intelligence.multimodal import read_document_bytes

        packed = read_document_bytes(str(document_id), str(ctx.restaurant_id))
        if not packed:
            return fail(
                code="media_required",
                message="I couldn't read that attachment to attach as evidence.",
            )
        data, mime, name = packed
    if not data:
        return fail(code="media_required", message="I need the incident and a photo to attach.")

    return attach_incident_photo_bytes(
        ctx,
        incident_id=incident_id,
        file_bytes=data,
        mime_type=mime or "image/jpeg",
        filename=name or "",
        caption=caption,
        media_id=media_id,
    )


def attach_incident_photo_bytes(
    ctx: OpsContext,
    *,
    incident_id: str,
    file_bytes: bytes,
    mime_type: str = "image/jpeg",
    filename: str = "",
    caption: str = "",
    media_id: str = "",
) -> OpsResult:
    """Attach photo evidence to an existing SafetyConcernReport and verify."""
    err = require_restaurant(ctx)
    if err:
        return err
    if not incident_id or not file_bytes:
        return fail(code="media_required", message="I need the incident and a photo to attach.")

    from staff.models_task import SafetyConcernReport
    from staff.incident_evidence import append_incident_photo_evidence, incident_has_photo_evidence

    ticket = SafetyConcernReport.objects.filter(id=incident_id, restaurant=ctx.restaurant).first()
    if not ticket:
        return fail(code="incident_not_found", message="I couldn't find that incident.")

    from miya.services.ops.context import guard_entity_location

    loc_err = guard_entity_location(ctx, ticket)
    if loc_err:
        return loc_err

    append_incident_photo_evidence(
        ticket,
        file_bytes=file_bytes,
        mime_type=mime_type or "image/jpeg",
        filename=filename or f"incident_{ticket.id}.jpg",
        media_id=media_id or "",
        caption=caption or "",
        source="whatsapp" if ctx.channel == "whatsapp" else "miya",
        submitted_by=ctx.user if getattr(ctx.user, "pk", None) else None,
    )
    ticket.refresh_from_db()
    if not incident_has_photo_evidence(ticket):
        return fail(
            code="verify_failed",
            message="I couldn't verify the photo was attached to the incident.",
        )
    try:
        from staff.incident_evidence import notify_owners_photo_attached

        notify_owners_photo_attached(ticket)
    except Exception:
        pass
    detail = _serialize_concern(ticket, detail=True)
    try:
        from core.operational_audit.service import INCIDENT_PHOTO_ATTACHED, record_operational_audit_event

        record_operational_audit_event(
            restaurant=ctx.restaurant,
            event_type=INCIDENT_PHOTO_ATTACHED,
            entity_type="incident",
            entity_id=str(ticket.id),
            entity_label=ticket.title or "",
            actor=ctx.user,
            location_id=ctx.location_id or "",
            channel=ctx.channel or "dashboard",
            operation_id=f"incident:photo:{ticket.id}:{media_id or filename or detail.get('photo_count')}",
            new_state={"photo_count": detail.get("photo_count")},
            summary=f"Photo attached to incident {str(ticket.id)[:8]}",
        )
    except Exception:
        pass
    return ok(
        message=f"Photo attached to incident {str(ticket.id)[:8]}.",
        verified=True,
        data={"incident": detail, "photo_count": detail.get("photo_count"), "has_photo": True},
    )


def route_incident(ctx: OpsContext, *, incident_id: str = "", incident_type: str = "") -> OpsResult:
    """Re-resolve and apply category owner for an open incident."""
    err = require_restaurant(ctx) or require_permission(ctx, "manage_widgets")
    if err:
        return err

    from staff.models_task import SafetyConcernReport
    from staff.incident_routing import resolve_default_assignee_for_incident_type

    ticket = None
    if incident_id:
        ticket = SafetyConcernReport.objects.filter(id=incident_id, restaurant=ctx.restaurant).first()
    if not ticket:
        return fail(code="incident_not_found", message="I couldn't find that incident to route.")

    itype = incident_type or ticket.incident_type or "General"
    assignee = resolve_default_assignee_for_incident_type(ctx.restaurant, itype)
    if not assignee:
        return fail(
            code="no_owner",
            message=f"No owner configured for '{itype}'. Set owners in Settings → Who owns what.",
        )
    ticket.assigned_to = assignee
    if incident_type:
        ticket.incident_type = itype
        ticket.save(update_fields=["assigned_to", "incident_type", "updated_at"])
    else:
        ticket.save(update_fields=["assigned_to", "updated_at"])

    fresh = SafetyConcernReport.objects.filter(id=ticket.id).first()
    if not fresh or fresh.assigned_to_id != assignee.id:
        return fail(code="verify_failed", message="I couldn't verify the incident was reassigned.")

    row = _serialize_concern(fresh)
    try:
        from core.operational_audit.service import INCIDENT_ROUTED, record_operational_audit_event

        record_operational_audit_event(
            restaurant=ctx.restaurant,
            event_type=INCIDENT_ROUTED,
            entity_type="incident",
            entity_id=str(fresh.id),
            entity_label=row.get("title") or "",
            actor=ctx.user,
            location_id=ctx.location_id or "",
            channel=ctx.channel or "dashboard",
            operation_id=f"incident:route:{fresh.id}:{assignee.id}",
            new_state={"assignee_id": str(assignee.id), "assignee_name": row.get("assignee_name")},
            summary=f"Incident routed to {row.get('assignee_name')}",
        )
    except Exception:
        pass
    return ok(
        message=f"Routed incident to {row.get('assignee_name')}.",
        verified=True,
        data={"incident": row, "audit_emitted": True},
    )


def _resolve_incident_row(ctx: OpsContext, *, incident_id: str = "", q: str = ""):
    from staff.models_task import SafetyConcernReport
    from miya.services.ops.context import guard_entity_location

    ticket = None
    iid = (incident_id or "").strip()
    if iid and iid.lower() not in ("it", "that", "this", "the incident"):
        ticket = SafetyConcernReport.objects.filter(id=iid, restaurant=ctx.restaurant).first()
        if not ticket and len(iid) >= 8:
            ticket = (
                SafetyConcernReport.objects.filter(restaurant=ctx.restaurant)
                .filter(id__istartswith=iid[:8])
                .order_by("-created_at")
                .first()
            )
        if ticket:
            loc_err = guard_entity_location(ctx, ticket)
            if loc_err:
                return None, loc_err
    if not ticket and q:
        found = find_incidents(ctx, q=q, status="ALL", limit=5)
        if found.success:
            rows = (found.data or {}).get("incidents") or []
            if len(rows) == 1:
                ticket = SafetyConcernReport.objects.filter(
                    id=rows[0]["id"], restaurant=ctx.restaurant
                ).first()
            elif len(rows) > 1:
                return None, fail(
                    code="needs_clarification",
                    message="Several incidents match — which one? Give the short id or a clearer keyword.",
                    needs_clarification=True,
                    data={"incidents": rows},
                )
    return ticket, None


def get_incident(
    ctx: OpsContext,
    *,
    incident_id: str = "",
    q: str = "",
) -> OpsResult:
    """Full incident detail including photo attachments (secure URLs)."""
    err = require_restaurant(ctx)
    if err:
        return err
    err = require_permission(ctx, None)
    if err:
        return err

    ticket, clar = _resolve_incident_row(ctx, incident_id=incident_id, q=q)
    if clar:
        return clar
    if not ticket:
        return fail(
            code="incident_not_found",
            message="I couldn't find that incident. Try a keyword (e.g. refrigerator) or the short id.",
        )

    detail = _serialize_concern(ticket, detail=True)
    photos = detail.get("photos") or []
    msg = (
        f"Incident {detail['id'][:8]} — {detail.get('title') or 'untitled'} "
        f"({detail.get('status')})"
    )
    if detail.get("has_photo"):
        msg += f", {detail.get('photo_count') or len(photos)} photo(s) on file."
    else:
        msg += ", no photo attached yet."
    return ok(
        message=msg,
        verified=True,
        data={
            "incident": detail,
            "has_photo": detail.get("has_photo"),
            "photo_count": detail.get("photo_count"),
            "photos": photos,
            "photo_urls": detail.get("photo_urls") or [],
            "attachments": detail.get("attachments") or [],
            "secure_photo_refs": [
                {
                    "storage_key": p.get("storage_key") or "",
                    "filename": p.get("filename") or "photo.jpg",
                    "mime_type": p.get("mime_type") or "image/jpeg",
                    "url": p.get("url") or "",
                }
                for p in photos
            ],
        },
        miya_directive=(
            "Relay status and whether a photo exists. "
            "For 'show me the photo' call get_incident_photo — do not invent image content."
        ),
    )


def get_incident_photo(
    ctx: OpsContext,
    *,
    incident_id: str = "",
    q: str = "",
    index: int = 0,
    phone: str = "",
) -> OpsResult:
    """
    Retrieve attached incident photo for Miya.
    On WhatsApp: send the stored image bytes when possible.
    Otherwise: return secure document/image references (presigned URL + storage key).
    """
    err = require_restaurant(ctx)
    if err:
        return err
    err = require_permission(ctx, None)
    if err:
        return err

    from staff.incident_evidence import list_incident_photos, load_incident_photo_bytes

    ticket, clar = _resolve_incident_row(ctx, incident_id=incident_id, q=q)
    if clar:
        return clar
    if not ticket:
        return fail(
            code="incident_not_found",
            message="I couldn't find that incident to show a photo for.",
        )

    photos = list_incident_photos(ticket)
    if not photos:
        return fail(
            code="photo_not_found",
            message=f"Incident {str(ticket.id)[:8]} has no photo attached yet.",
            data={"incident_id": str(ticket.id), "has_photo": False},
        )

    idx = max(0, min(int(index or 0), len(photos) - 1))
    chosen = photos[idx]
    secure_refs = [
        {
            "storage_key": p.get("storage_key") or "",
            "filename": p.get("filename") or "photo.jpg",
            "mime_type": p.get("mime_type") or "image/jpeg",
            "url": p.get("url") or "",
            "index": i,
        }
        for i, p in enumerate(photos)
    ]

    whatsapp_sent = False
    send_error = ""
    if ctx.channel == "whatsapp":
        to_phone = (
            (phone or "").strip()
            or (getattr(ctx.user, "phone", None) or "").strip()
        )
        if to_phone:
            loaded = load_incident_photo_bytes(ticket, index=idx)
            if loaded:
                file_bytes, mime, filename = loaded
                try:
                    from notifications.services import notification_service

                    ok_send, meta = notification_service.send_whatsapp_media_attachment(
                        to_phone,
                        file_bytes=file_bytes,
                        mime_type=mime or "image/jpeg",
                        filename=filename or "incident.jpg",
                        caption=(
                            f"Photo for incident {str(ticket.id)[:8]}: "
                            f"{getattr(ticket, 'title', None) or 'incident'}"
                        )[:900],
                    )
                    whatsapp_sent = bool(ok_send)
                    if not ok_send:
                        send_error = str((meta or {}).get("error") or "send_failed")
                except Exception as exc:
                    send_error = str(exc)
            else:
                send_error = "could_not_load_bytes"

    if whatsapp_sent:
        msg = (
            f"Here's the photo attached to incident {str(ticket.id)[:8]} "
            f"({chosen.get('filename') or 'photo'}). Sent on WhatsApp."
        )
    elif ctx.channel == "whatsapp" and send_error:
        msg = (
            f"Incident {str(ticket.id)[:8]} has a photo on file "
            f"({chosen.get('filename') or 'photo'}), but I couldn't deliver it on WhatsApp. "
            "Open Incidents on the dashboard to view it."
        )
    else:
        msg = (
            f"Photo on file for incident {str(ticket.id)[:8]}: "
            f"{chosen.get('filename') or 'photo.jpg'}. "
            "Open Checklist & Incidences on the dashboard to view the image."
        )

    return ok(
        message=msg,
        verified=True,
        data={
            "incident_id": str(ticket.id),
            "title": getattr(ticket, "title", None) or "",
            "has_photo": True,
            "photo_count": len(photos),
            "photo_index": idx,
            "photo": chosen,
            "photos": photos,
            "photo_urls": [p["url"] for p in photos if p.get("url")],
            "attachments": [
                {
                    "url": p["url"],
                    "name": p.get("filename") or "photo.jpg",
                    "content_type": p.get("mime_type") or "image/jpeg",
                }
                for p in photos
                if p.get("url")
            ],
            "secure_photo_refs": secure_refs,
            "whatsapp_image_sent": whatsapp_sent,
            "whatsapp_send_error": send_error or None,
        },
        miya_directive=(
            "If whatsapp_image_sent=true, tell the user you sent the photo — do not paste URLs. "
            "Otherwise say the photo is on file and point them to Incidents on the dashboard. "
            "Never invent what the image shows."
        ),
    )


def resolve_incident(
    ctx: OpsContext,
    *,
    incident_id: str = "",
    q: str = "",
    resolution_notes: str = "",
) -> OpsResult:
    """Close / resolve a SafetyConcernReport (canonical status track → RESOLVED)."""
    err = require_restaurant(ctx) or require_permission(ctx, "manage_widgets")
    if err:
        return err

    from django.utils import timezone
    from staff.models_task import SafetyConcernReport

    ticket, clar = _resolve_incident_row(ctx, incident_id=incident_id, q=q)
    if clar:
        return clar
    if not ticket:
        return fail(code="incident_not_found", message="I couldn't find that incident to close.")

    from miya.services.ops.context import guard_entity_location

    loc_err = guard_entity_location(ctx, ticket)
    if loc_err:
        return loc_err

    old_status = ticket.status
    notes = (resolution_notes or "").strip() or "Closed via Miya"
    ticket.status = "RESOLVED"
    ticket.resolved_at = timezone.now()
    ticket.resolution_notes = notes
    try:
        ticket.resolved_by = ctx.user if getattr(ctx.user, "pk", None) else None
    except Exception:
        pass
    update_fields = ["status", "resolved_at", "resolution_notes", "updated_at"]
    if hasattr(ticket, "resolved_by"):
        update_fields.append("resolved_by")
    ticket.save(update_fields=update_fields)

    fresh = SafetyConcernReport.objects.filter(id=ticket.id, restaurant=ctx.restaurant).first()
    if not fresh or fresh.status != "RESOLVED":
        return fail(code="verify_failed", message="I couldn't verify the incident was closed.")

    try:
        from staff.views_agent import _invalidate_staff_incidents_cache

        _invalidate_staff_incidents_cache(ctx.restaurant.id)
    except Exception:
        pass

    row = _serialize_concern(fresh, detail=True)
    try:
        from core.operational_audit.service import INCIDENT_RESOLVED, record_operational_audit_event

        record_operational_audit_event(
            restaurant=ctx.restaurant,
            event_type=INCIDENT_RESOLVED,
            entity_type="incident",
            entity_id=str(fresh.id),
            entity_label=row.get("title") or "",
            actor=ctx.user,
            location_id=ctx.location_id or "",
            channel=ctx.channel or "dashboard",
            operation_id=f"incident:resolve:{fresh.id}",
            previous_state={"status": old_status},
            new_state={"status": "RESOLVED", "resolution_notes": notes[:200]},
            summary=f"Incident resolved: {row.get('title') or str(fresh.id)[:8]}",
        )
    except Exception:
        pass
    return ok(
        message=f"Incident {row['id'][:8]} marked RESOLVED.",
        verified=True,
        data={"incident": row, "audit_emitted": True},
    )
