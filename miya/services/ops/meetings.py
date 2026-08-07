"""Canonical meetings + reminders — Dashboard / Miya / WhatsApp / Calendar parity."""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from miya.services.ops.context import OpsContext, require_permission, require_restaurant
from miya.services.ops.result import OpsResult, clarify, fail, ok


def _parse_due(raw: str):
    if not raw:
        return None
    due = parse_datetime(str(raw).replace("Z", "+00:00"))
    if due is None:
        return None
    if timezone.is_naive(due):
        due = timezone.make_aware(due)
    return due


def _serialize_reminder(rem) -> dict[str, Any]:
    from scheduling.calendar_reminder_sync import gcal_event_id_from_body, meeting_kind_from_text

    gcal_id = gcal_event_id_from_body(rem.body or "")
    kind = meeting_kind_from_text(rem.title or "", rem.body or "")
    source = "compliance" if rem.linked_compliance_document_id else ("calendar" if gcal_id else "personal")
    return {
        "id": str(rem.id),
        "reminder_id": str(rem.id),
        "title": rem.title,
        "body": rem.body or "",
        "due_at": rem.due_at.isoformat() if rem.due_at else None,
        "status": rem.status,
        "recurrence": rem.recurrence,
        "source": source,
        "kind": "reminder",
        "meeting_kind": kind,
        "event_id": gcal_id,
        "linked_compliance_document_id": (
            str(rem.linked_compliance_document_id) if rem.linked_compliance_document_id else None
        ),
    }


def _serialize_calendar_row(row: dict[str, Any]) -> dict[str, Any]:
    from scheduling.calendar_reminder_sync import meeting_kind_from_text

    title = row.get("title") or row.get("summary") or ""
    desc = row.get("description") or ""
    kind = meeting_kind_from_text(title, desc)
    return {
        "id": str(row.get("id") or ""),
        "event_id": str(row.get("id") or ""),
        "title": title,
        "start": row.get("start"),
        "end": row.get("end"),
        "location": row.get("location") or "",
        "description": desc,
        "html_link": row.get("html_link") or row.get("htmlLink"),
        "kind": "meeting",
        "source": "google_calendar",
        "meeting_kind": kind,
        "status": row.get("status") or "PENDING",
    }


def list_meetings(
    ctx: OpsContext,
    *,
    q: str = "",
    meeting_kind: str = "",
    days: int = 14,
    limit: int = 20,
    include_reminders: bool = True,
) -> OpsResult:
    """
    Unified agenda: Google Calendar + personal/compliance reminders.
    Same projection Dashboard Meetings widget + Miya + WhatsApp should reason about.
    """
    err = require_restaurant(ctx)
    if err:
        return err

    from scheduling.calendar_reminder_sync import normalize_meeting_kind

    needle = (q or "").strip()
    kind_filter = normalize_meeting_kind(meeting_kind)
    lim = max(1, min(int(limit or 20), 40))
    horizon_days = max(1, min(int(days or 14), 60))
    now = timezone.now()
    items: list[dict[str, Any]] = []
    calendar_connected = False

    try:
        from dashboard.api.meetings_reminders import _get_valid_access_token
        from dashboard.api.calendar_write import _fetch_calendar_events_for_agent

        access_token, _gcal = _get_valid_access_token(ctx.restaurant)
        if access_token:
            calendar_connected = True
            rows = (
                _fetch_calendar_events_for_agent(
                    access_token,
                    ctx.user,
                    past_hours=6,
                    future_hours=24 * horizon_days,
                    max_results=40,
                )
                or []
            )
            for row in rows:
                ser = _serialize_calendar_row(row)
                if needle:
                    hay = f"{ser['title']} {ser.get('location') or ''} {ser.get('description') or ''}".lower()
                    if needle.lower() not in hay:
                        continue
                if kind_filter and ser.get("meeting_kind") != kind_filter:
                    continue
                items.append(ser)
    except Exception:
        pass

    if include_reminders:
        try:
            from scheduling.memory_models import PersonalReminder
            from scheduling.calendar_reminder_sync import gcal_event_id_from_body

            qs = PersonalReminder.objects.filter(
                restaurant=ctx.restaurant,
                owner=ctx.user,
                status="pending",
                due_at__gte=now - timedelta(hours=6),
                due_at__lte=now + timedelta(days=horizon_days),
            ).order_by("due_at")
            if needle:
                qs = qs.filter(Q(title__icontains=needle) | Q(body__icontains=needle))
            seen_gcal: set[str] = {str(i.get("event_id") or "") for i in items if i.get("event_id")}
            for rem in qs[: lim * 2]:
                ser = _serialize_reminder(rem)
                if kind_filter and ser.get("meeting_kind") != kind_filter:
                    continue
                gid = ser.get("event_id") or gcal_event_id_from_body(rem.body or "")
                if gid and gid in seen_gcal:
                    continue
                items.append(ser)
        except Exception:
            pass

    def _sort_key(row: dict) -> str:
        return str(row.get("start") or row.get("due_at") or "")

    items.sort(key=_sort_key)
    items = items[:lim]

    if not items:
        where = f" matching '{needle}'" if needle else ""
        kind_bit = f" ({kind_filter})" if kind_filter else ""
        return fail(
            code="meetings_not_found",
            message=f"No meetings or reminders{where}{kind_bit} in the next {horizon_days} days.",
            data={
                "items": [],
                "events": [],
                "reminders": [],
                "count": 0,
                "calendar_connected": calendar_connected,
            },
        )

    meetings = [i for i in items if i.get("kind") == "meeting"]
    reminders = [i for i in items if i.get("kind") == "reminder"]
    return ok(
        message=f"Found {len(items)} item(s) on your agenda.",
        verified=True,
        data={
            "items": items,
            "events": meetings,
            "reminders": reminders,
            "count": len(items),
            "calendar_connected": calendar_connected,
        },
        miya_directive=(
            "Relay titles and times from this payload. Dashboard Meetings & Reminders, "
            "Google Calendar, and WhatsApp reminders use the same events."
        ),
    )


def list_calendar_events(ctx: OpsContext, *, q: str = "", days: int = 14, limit: int = 20) -> OpsResult:
    return list_meetings(ctx, q=q, days=days, limit=limit, include_reminders=True)


def create_calendar_event(
    ctx: OpsContext,
    *,
    title: str = "",
    start: str = "",
    end: str = "",
    description: str = "",
    location: str = "",
    meeting_kind: str = "",
    is_reminder: bool = False,
    events: list | None = None,
    attendees: list | None = None,
) -> OpsResult:
    err = require_restaurant(ctx) or require_permission(ctx, "manage_widgets")
    if err:
        return err

    from dashboard.api.calendar_write import _create_single_calendar_event
    from scheduling.calendar_reminder_sync import normalize_meeting_kind, title_with_meeting_kind
    from scheduling.memory_models import PersonalReminder

    batch = events if isinstance(events, list) and events else None
    if batch:
        created = []
        errors = []
        for i, item in enumerate(batch[:20]):
            if not isinstance(item, dict):
                errors.append({"index": i, "error": "invalid_item"})
                continue
            one = create_calendar_event(
                ctx,
                title=str(item.get("title") or item.get("summary") or ""),
                start=str(item.get("start") or item.get("start_at") or ""),
                end=str(item.get("end") or item.get("end_at") or ""),
                description=str(item.get("description") or item.get("notes") or ""),
                location=str(item.get("location") or ""),
                meeting_kind=str(item.get("meeting_kind") or item.get("department") or meeting_kind or ""),
                is_reminder=bool(item.get("is_reminder")),
                attendees=item.get("attendees") if isinstance(item.get("attendees"), list) else attendees,
            )
            if one.success:
                created.append(one.data)
            else:
                errors.append({"index": i, "code": one.code, "message": one.message_for_user})
        if not created:
            return fail(
                code=errors[0].get("code") if errors else "batch_failed",
                message=(errors[0].get("message") if errors else "Couldn't create those meetings."),
                data={"errors": errors},
            )
        titles = [c.get("title") or "meeting" for c in created]
        return ok(
            message=(
                f"Created {len(created)} meeting(s): "
                + ", ".join(f'"{t}"' for t in titles[:5])
                + ("…" if len(titles) > 5 else "")
                + "."
            ),
            verified=True,
            data={"created_count": len(created), "events": created, "errors": errors},
        )

    kind = normalize_meeting_kind(meeting_kind)
    final_title = title_with_meeting_kind((title or "").strip(), kind)
    if not final_title:
        return fail(code="title_required", message="I need a title for the meeting.")
    if not (start or "").strip():
        return fail(code="start_required", message="I need a start time for the meeting.")

    payload = {
        "title": final_title,
        "start": start,
        "end": end or "",
        "description": description or "",
        "location": location or "",
        "is_reminder": is_reminder,
        "meeting_kind": kind or "",
        "attendees": attendees or [],
    }
    raw = _create_single_calendar_event(ctx.restaurant, payload, acting_user=ctx.user)
    if not raw.get("success"):
        return fail(
            code=str(raw.get("error") or "calendar_create_failed"),
            message=str(raw.get("message_for_user") or "Couldn't create that calendar event."),
            data={k: raw.get(k) for k in ("connect_url", "connected", "detail") if raw.get(k) is not None},
        )

    event_id = str(raw.get("event_id") or "")
    rem = None
    if event_id:
        rem = (
            PersonalReminder.objects.filter(
                restaurant=ctx.restaurant,
                owner=ctx.user,
                body__icontains=f"gcal_event_id:{event_id}",
                status="pending",
            )
            .order_by("-created_at")
            .first()
        )

    if not rem and event_id and getattr(ctx.user, "pk", None):
        return fail(
            code="verify_partial",
            message=(
                f"Calendar event created but WhatsApp reminder sync failed for \"{final_title}\". "
                "The event is on Google Calendar — try listing meetings."
            ),
            data={
                "event_id": event_id,
                "title": final_title,
                "calendar_event": raw.get("calendar_event"),
                "html_link": raw.get("html_link"),
                "meeting_kind": kind,
            },
        )

    return ok(
        message=raw.get("message_for_user") or f'Created meeting "{final_title}".',
        verified=True,
        data={
            "event_id": event_id,
            "title": final_title,
            "meeting_kind": kind,
            "calendar_event": raw.get("calendar_event"),
            "html_link": raw.get("html_link"),
            "reminder": _serialize_reminder(rem) if rem else None,
            "surfaces": ["google_calendar", "dashboard", "whatsapp", "miya"],
        },
        miya_directive="Confirm with title/time. Same event appears on Calendar, Dashboard, and WhatsApp.",
    )


def update_calendar_event(
    ctx: OpsContext,
    *,
    event_id: str = "",
    q: str = "",
    title: str = "",
    start: str = "",
    end: str = "",
    location: str = "",
    description: str = "",
) -> OpsResult:
    err = require_restaurant(ctx) or require_permission(ctx, "manage_widgets")
    if err:
        return err

    from dashboard.api.calendar_write import _update_single_calendar_event
    from scheduling.memory_models import PersonalReminder

    payload: dict[str, Any] = {}
    if event_id:
        payload["event_id"] = event_id
    if q:
        payload["q"] = q
    if title:
        payload["title"] = title
    if start:
        payload["start"] = start
    if end:
        payload["end"] = end
    if location:
        payload["location"] = location
    if description:
        payload["description"] = description

    raw = _update_single_calendar_event(ctx.restaurant, payload, acting_user=ctx.user)
    if not raw.get("success"):
        if raw.get("error") == "ambiguous_event":
            return clarify(
                message=raw.get("message_for_user") or "Several meetings match — which one?",
                data={"matches": raw.get("matches") or []},
            )
        return fail(
            code=str(raw.get("error") or "calendar_update_failed"),
            message=str(raw.get("message_for_user") or "Couldn't update that meeting."),
            data={"matches": raw.get("matches")} if raw.get("matches") else {},
        )

    eid = str(raw.get("event_id") or event_id)
    rem = None
    if eid:
        rem = (
            PersonalReminder.objects.filter(
                restaurant=ctx.restaurant,
                body__icontains=f"gcal_event_id:{eid}",
                status="pending",
            )
            .order_by("-updated_at")
            .first()
        )

    return ok(
        message=raw.get("message_for_user") or "Meeting updated.",
        verified=True,
        data={
            "event_id": eid,
            "calendar_event": raw.get("calendar_event"),
            "reminder": _serialize_reminder(rem) if rem else None,
            "updated_fields": raw.get("updated_fields") or [],
            "surfaces": ["google_calendar", "dashboard", "whatsapp", "miya"],
        },
    )


def delete_calendar_event(ctx: OpsContext, *, event_id: str = "", q: str = "") -> OpsResult:
    err = require_restaurant(ctx) or require_permission(ctx, "manage_widgets")
    if err:
        return err

    from dashboard.api.calendar_write import _delete_single_calendar_event
    from scheduling.memory_models import PersonalReminder

    raw = _delete_single_calendar_event(
        ctx.restaurant,
        {"event_id": event_id, "q": q},
        acting_user=ctx.user,
    )
    if not raw.get("success"):
        if raw.get("error") == "ambiguous_event":
            return clarify(
                message=raw.get("message_for_user") or "Several meetings match — which one?",
                data={"matches": raw.get("matches") or []},
            )
        return fail(
            code=str(raw.get("error") or "calendar_delete_failed"),
            message=str(raw.get("message_for_user") or "Couldn't remove that meeting."),
        )

    eid = str(raw.get("event_id") or event_id)
    pending = PersonalReminder.objects.filter(
        restaurant=ctx.restaurant,
        body__icontains=f"gcal_event_id:{eid}",
        status="pending",
    ).count()
    if pending:
        return fail(
            code="verify_failed",
            message="Calendar event removed but WhatsApp reminder is still pending — try again.",
            data={"event_id": eid},
        )

    return ok(
        message=raw.get("message_for_user") or "Meeting removed.",
        verified=True,
        data={
            "event_id": eid,
            "deleted_title": raw.get("deleted_title"),
            "surfaces": ["google_calendar", "dashboard", "whatsapp"],
        },
    )


def create_personal_reminder(
    ctx: OpsContext,
    *,
    title: str = "",
    due_at: str = "",
    body: str = "",
    recurrence: str = "none",
    reminder_kind: str = "",
) -> OpsResult:
    """WhatsApp-fireable reminder — Dashboard Meetings widget + Miya schedule share it."""
    err = require_restaurant(ctx)
    if err:
        return err

    from scheduling.memory_models import PersonalReminder

    text = (title or "").strip()
    if not text:
        return fail(code="title_required", message="What should I remind you about?")

    due = _parse_due(due_at)
    if due is None:
        return fail(code="due_at_required", message="When should I remind you? Give a date and time.")
    if due < timezone.now():
        return fail(code="due_at_in_past", message="That reminder time is already in the past.")

    rec = (recurrence or "none").lower()
    if rec not in ("none", "daily", "weekly", "monthly", "weekdays"):
        rec = "none"

    kind = (reminder_kind or "").strip().lower()
    notes = (body or "").strip()
    if kind == "daily" and rec == "none":
        rec = "daily"
    if kind in ("task", "insurance", "compliance") and kind not in notes.lower():
        notes = f"reminder_kind:{kind}\n{notes}".strip()

    phone = re.sub(r"\D", "", str(getattr(ctx.user, "phone", None) or ""))
    rem = PersonalReminder.objects.create(
        restaurant=ctx.restaurant,
        owner=ctx.user,
        phone=phone[:40],
        title=text[:255],
        body=notes[:4000],
        due_at=due,
        timezone_name=str(getattr(ctx.restaurant, "timezone", None) or "Africa/Casablanca")[:64],
        recurrence=rec,
        approach_nudges_sent=[],
    )
    fresh = PersonalReminder.objects.filter(id=rem.id, status="pending").first()
    if not fresh:
        return fail(code="verify_failed", message="I tried to save the reminder but couldn't verify it.")

    when = fresh.due_at.strftime("%a %b %d, %H:%M")
    rec_bit = f" ({rec})" if rec != "none" else ""
    return ok(
        message=f"Got it — I'll remind you on WhatsApp about *{fresh.title}* on {when}{rec_bit}.",
        verified=True,
        data={
            "reminder": _serialize_reminder(fresh),
            "reminder_id": str(fresh.id),
            "surfaces": ["dashboard", "whatsapp", "miya"],
        },
    )


def list_reminders(
    ctx: OpsContext,
    *,
    q: str = "",
    status: str = "pending",
    limit: int = 20,
) -> OpsResult:
    err = require_restaurant(ctx)
    if err:
        return err

    from scheduling.memory_models import PersonalReminder

    qs = PersonalReminder.objects.filter(restaurant=ctx.restaurant, owner=ctx.user)
    st = (status or "pending").lower()
    if st != "all":
        qs = qs.filter(status=st)
    needle = (q or "").strip()
    if needle:
        qs = qs.filter(Q(title__icontains=needle) | Q(body__icontains=needle))
    rows = [_serialize_reminder(r) for r in qs.order_by("due_at")[: max(1, min(int(limit or 20), 50))]]
    if not rows:
        return fail(
            code="reminders_not_found",
            message="No reminders match that filter.",
            data={"reminders": [], "count": 0},
        )
    return ok(
        message=f"Found {len(rows)} reminder(s).",
        verified=True,
        data={"reminders": rows, "count": len(rows)},
    )


def cancel_reminder(ctx: OpsContext, *, reminder_id: str = "", q: str = "") -> OpsResult:
    err = require_restaurant(ctx)
    if err:
        return err

    from scheduling.memory_models import PersonalReminder

    rem = None
    rid = (reminder_id or "").strip()
    if rid:
        rem = PersonalReminder.objects.filter(
            id=rid, restaurant=ctx.restaurant, owner=ctx.user
        ).first()
    elif q:
        matches = list(
            PersonalReminder.objects.filter(
                restaurant=ctx.restaurant,
                owner=ctx.user,
                status="pending",
            )
            .filter(Q(title__icontains=q) | Q(body__icontains=q))
            .order_by("due_at")[:5]
        )
        if len(matches) > 1:
            return clarify(
                message="Several reminders match — which one should I cancel?",
                data={"candidates": [_serialize_reminder(m) for m in matches]},
            )
        rem = matches[0] if matches else None

    if not rem:
        return fail(code="reminder_not_found", message="I couldn't find that reminder.")

    rem.status = "cancelled"
    rem.save(update_fields=["status", "updated_at"])
    fresh = PersonalReminder.objects.filter(id=rem.id).first()
    if not fresh or fresh.status != "cancelled":
        return fail(code="verify_failed", message="I couldn't verify the cancellation.")
    return ok(
        message=f"Cancelled reminder: *{fresh.title}*.",
        verified=True,
        data={"reminder": _serialize_reminder(fresh)},
    )


def sync_compliance_reminder(ctx: OpsContext, *, document_id: str = "", q: str = "") -> OpsResult:
    """Ensure insurance/compliance expiry reminders exist (Dashboard + WhatsApp)."""
    err = require_restaurant(ctx) or require_permission(ctx, "manage_settings")
    if err:
        return err

    from payroll.models import ComplianceDocument
    from payroll.services.compliance_reminder_sync import sync_compliance_document_reminder
    from scheduling.memory_models import PersonalReminder

    doc = None
    did = (document_id or "").strip()
    if did:
        doc = ComplianceDocument.objects.filter(id=did, restaurant=ctx.restaurant).first()
    elif q:
        qs = ComplianceDocument.objects.filter(
            restaurant=ctx.restaurant,
            status=ComplianceDocument.STATUS_ACTIVE,
        ).filter(Q(title__icontains=q) | Q(document_type__icontains=q))
        rows = list(qs[:5])
        if len(rows) > 1:
            return clarify(
                message="Several compliance documents match — which one?",
                data={
                    "candidates": [
                        {
                            "id": str(d.id),
                            "title": d.title,
                            "expires_at": d.expires_at.isoformat() if d.expires_at else None,
                        }
                        for d in rows
                    ]
                },
            )
        doc = rows[0] if rows else None

    if not doc:
        return fail(code="document_not_found", message="I couldn't find that compliance/insurance document.")

    summary = sync_compliance_document_reminder(doc, owner=ctx.user, reset_nudges=False)
    rem = PersonalReminder.objects.filter(linked_compliance_document=doc, status="pending").first()
    if not rem and doc.expires_at:
        return fail(
            code="verify_failed",
            message="I tried to sync the expiry reminder but couldn't verify it was saved.",
            data={"sync": summary},
        )
    due_s = rem.due_at.date().isoformat() if rem and rem.due_at else "synced"
    return ok(
        message=f"Expiry reminder set for *{doc.title}* — due {due_s}.",
        verified=True,
        data={
            "document_id": str(doc.id),
            "reminder": _serialize_reminder(rem) if rem else None,
            "sync": summary,
            "surfaces": ["dashboard", "whatsapp", "miya"],
        },
    )


def confirm_meeting(ctx: OpsContext, *, q: str = "", event_id: str = "") -> OpsResult:
    """Confirm attendance for a meeting-linked personal reminder."""
    err = require_restaurant(ctx)
    if err:
        return err

    needle = (q or event_id or "").strip()
    if not needle:
        return clarify(message="Which meeting should I confirm? Give me the title or time.")

    from scheduling.memory_models import PersonalReminder

    qs = PersonalReminder.objects.filter(
        restaurant=ctx.restaurant,
        owner=ctx.user,
        status="pending",
        due_at__gte=timezone.now() - timedelta(days=1),
    ).filter(Q(title__icontains=needle) | Q(body__icontains=needle)).order_by("due_at")

    matches = list(qs[:5])
    if not matches:
        return fail(
            code="meeting_not_found",
            message=(
                f"I couldn't find an upcoming meeting matching '{needle}'. "
                "Try list_meetings, or open Calendar."
            ),
        )
    if len(matches) > 1:
        return clarify(
            message="Several meetings match — which one?",
            data={
                "candidates": [
                    {"id": str(m.id), "title": m.title, "due_at": m.due_at.isoformat()}
                    for m in matches
                ]
            },
        )

    rem = matches[0]
    rem.status = "cancelled"
    rem.save(update_fields=["status", "updated_at"])
    fresh = PersonalReminder.objects.filter(id=rem.id).first()
    if not fresh or fresh.status != "cancelled":
        return fail(code="verify_failed", message="I couldn't verify the meeting confirmation.")

    return ok(
        message=f"Confirmed: *{fresh.title}* — I've noted you're set for it.",
        verified=True,
        data={"reminder_id": str(fresh.id), "title": fresh.title, "status": fresh.status},
    )
