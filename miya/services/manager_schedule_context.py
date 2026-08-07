"""Inject manager calendar, reminders, and schedule into Miya's system prompt."""
from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

_MANAGER_ROLES = frozenset({"OWNER", "MANAGER", "ADMIN"})


def build_manager_schedule_block(user, restaurant) -> str:
    """
    Upcoming Google Calendar events, personal reminders, and today's shifts —
    so Miya knows the manager's agenda without extra tool calls.
    """
    if restaurant is None or user is None:
        return ""
    if (getattr(user, "role", None) or "").upper() not in _MANAGER_ROLES:
        return ""

    lines: list[str] = []
    now = timezone.now()
    horizon = now + timedelta(days=14)

    # Google Calendar
    try:
        from dashboard.api.meetings_reminders import _get_valid_access_token
        from dashboard.api.calendar_write import _fetch_calendar_events_for_agent

        access_token, gcal = _get_valid_access_token(restaurant)
        if access_token:
            rows = _fetch_calendar_events_for_agent(
                access_token,
                user,
                past_hours=6,
                future_hours=24 * 14,
                max_results=15,
            ) or []
            if rows:
                lines.append("Google Calendar (use event_id with update/delete_calendar_event):")
                for row in rows[:8]:
                    when = (row.get("start") or "").replace("T", " ")[:16]
                    loc = row.get("location") or ""
                    loc_bit = f", {loc}" if loc else ""
                    lines.append(
                        f"  • {row.get('title')} — {when}{loc_bit} (event_id={row.get('id')})"
                    )
            else:
                lines.append("Google Calendar: connected, no events in the next 2 weeks.")
        else:
            email = (gcal or {}).get("email") or ""
            hint = f" ({email})" if email else ""
            lines.append(
                f"Google Calendar: not connected{hint} — manager can connect in Settings → Integrations."
            )
    except Exception:
        lines.append("Google Calendar: unavailable this turn.")

    # Personal reminders (Meetings & Reminders widget + WhatsApp pings)
    try:
        from scheduling.memory_models import PersonalReminder

        reminders = list(
            PersonalReminder.objects.filter(
                restaurant=restaurant,
                owner=user,
                status="pending",
                due_at__lte=horizon,
            )
            .order_by("due_at")[:12]
        )
        if reminders:
            lines.append("Personal reminders (create_personal_reminder / WhatsApp pings):")
            for rem in reminders:
                due_s = rem.due_at.strftime("%Y-%m-%d %H:%M") if rem.due_at else "?"
                linked = "compliance" if rem.linked_compliance_document_id else "personal"
                lines.append(f"  • {rem.title} — due {due_s} ({linked}, id={rem.id})")
        else:
            lines.append("Personal reminders: none pending in the next 2 weeks.")
    except Exception:
        pass

    # Today's staff schedule (restaurant-wide)
    try:
        from scheduling.models import AssignedShift

        today = now.date()
        shifts = list(
            AssignedShift.objects.filter(
                restaurant=restaurant,
                shift_date=today,
            )
            .select_related("user")
            .order_by("start_time")[:20]
        )
        if shifts:
            lines.append(f"Staff shifts today ({today.isoformat()}):")
            for sh in shifts[:10]:
                name = ""
                if sh.user:
                    name = f"{sh.user.first_name or ''} {sh.user.last_name or ''}".strip() or sh.user.email
                start = sh.start_time.strftime("%H:%M") if sh.start_time else "?"
                end = sh.end_time.strftime("%H:%M") if sh.end_time else "?"
                lines.append(f"  • {name or 'Unassigned'} {start}–{end}")
            if len(shifts) > 10:
                lines.append(f"  • (+{len(shifts) - 10} more shifts — list_shifts for full roster)")
        else:
            lines.append(f"Staff shifts today ({today.isoformat()}): none scheduled.")
    except Exception:
        pass

    if not lines:
        return ""
    return (
        "\n[MANAGER SCHEDULE — calendar, reminders, today's shifts; authoritative for this manager]\n"
        + "\n".join(lines)
        + "\nMiya proactively pings this manager on WhatsApp before reminders and meetings.\n"
    )
