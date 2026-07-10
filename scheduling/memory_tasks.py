"""
Celery tasks for WhatsApp personal reminders and daily briefings.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)


def _next_due(rem):
    from datetime import timedelta as td

    try:
        from dateutil.relativedelta import relativedelta
    except ImportError:
        relativedelta = None

    if rem.recurrence == "daily":
        return rem.due_at + td(days=1)
    if rem.recurrence == "weekly":
        return rem.due_at + td(weeks=1)
    if rem.recurrence == "monthly":
        if relativedelta:
            return rem.due_at + relativedelta(months=1)
        return rem.due_at + td(days=30)
    if rem.recurrence == "weekdays":
        nxt = rem.due_at + td(days=1)
        while nxt.weekday() >= 5:
            nxt = nxt + td(days=1)
        return nxt
    return None


@shared_task(name="scheduling.memory_tasks.personal_reminder_sweep")
def personal_reminder_sweep():
    """
    Fire due personal reminders via WhatsApp (free-form inside 24h window).
    Runs every minute via Celery beat.
    """
    from scheduling.memory_models import PersonalReminder
    from notifications.services import notification_service

    now = timezone.now()
    due = list(
        PersonalReminder.objects.filter(
            status="pending",
            due_at__lte=now + timedelta(minutes=1),
        )
        .select_related("owner", "restaurant", "linked_note")
        .order_by("due_at")[:200]
    )
    sent = 0
    failed = 0
    for rem in due:
        phone = rem.phone or re.sub(r"\D", "", str(getattr(rem.owner, "phone", "") or ""))
        if not phone:
            rem.status = "failed"
            rem.save(update_fields=["status", "updated_at"])
            failed += 1
            continue

        body_parts = [f"⏰ Reminder: {rem.title}"]
        if rem.body:
            body_parts.append(rem.body)
        if rem.linked_note_id and rem.linked_note:
            preview = (rem.linked_note.content or "")[:160]
            body_parts.append(f"Related note: {preview}")
        text = "\n".join(body_parts)

        try:
            result = notification_service.send_whatsapp_text(phone, text)
            ok = result[0] if isinstance(result, tuple) else bool(result)
            if not ok:
                logger.warning("personal_reminder_sweep: WA send failed for %s", rem.id)
                failed += 1
                continue
        except Exception:
            logger.exception("personal_reminder_sweep send error rem=%s", rem.id)
            failed += 1
            continue

        rem.fired_at = now
        rem.fire_count = (rem.fire_count or 0) + 1
        nxt = _next_due(rem)
        if nxt:
            rem.due_at = nxt
            rem.status = "pending"
        else:
            rem.status = "fired"
        rem.save()
        sent += 1

    return {"sent": sent, "failed": failed, "checked": len(due)}


@shared_task(name="scheduling.memory_tasks.daily_briefing_sweep")
def daily_briefing_sweep():
    """
    Morning personal briefing for managers/staff with phone on file.
    Managers get their personal Memorae briefing on WhatsApp even with no open tasks.
    Default: 07:30 Africa/Casablanca — configured via beat schedule.
    """
    from accounts.models import CustomUser
    from scheduling.memory_models import MemoryList, MemoryNote, PersonalReminder
    from dashboard.models import Task
    from notifications.services import notification_service

    now = timezone.now()
    end = now + timedelta(hours=36)
    sent = 0

    # Anyone with pending reminders, open tasks, OR manager/owner/admin role
    owner_ids = set(
        PersonalReminder.objects.filter(
            status="pending", due_at__lte=end
        ).values_list("owner_id", flat=True)
    )
    owner_ids |= set(
        Task.objects.filter(
            status__in=["PENDING", "IN_PROGRESS"],
            assigned_to__isnull=False,
        ).values_list("assigned_to_id", flat=True)[:500]
    )
    # Managers always eligible for morning briefing on WhatsApp
    owner_ids |= set(
        CustomUser.objects.filter(
            role__in=["MANAGER", "OWNER", "ADMIN"],
            is_active=True,
            phone__isnull=False,
        )
        .exclude(phone="")
        .values_list("id", flat=True)[:500]
    )

    users = CustomUser.objects.filter(id__in=owner_ids).select_related("restaurant")
    for user in users:
        restaurant = getattr(user, "restaurant", None)
        if not restaurant:
            continue
        phone = re.sub(r"\D", "", str(getattr(user, "phone", "") or ""))
        if not phone or len(phone) < 8:
            continue

        reminders = list(
            PersonalReminder.objects.filter(
                restaurant=restaurant,
                owner=user,
                status="pending",
                due_at__lte=end,
            ).order_by("due_at")[:8]
        )
        open_tasks = list(
            Task.objects.filter(
                restaurant=restaurant,
                assigned_to=user,
                status__in=["PENDING", "IN_PROGRESS"],
            ).order_by("due_date")[:8]
        )
        lists = MemoryList.objects.filter(
            restaurant=restaurant, owner=user, is_archived=False
        ).prefetch_related("items")[:5]
        list_bits = []
        for lst in lists:
            open_items = [i.text for i in lst.items.all() if not i.is_checked][:4]
            if open_items:
                list_bits.append(f"{lst.name}: " + "; ".join(open_items))

        recent = list(
            MemoryNote.objects.filter(restaurant=restaurant, is_archived=False)
            .filter(Q(owner=user) | Q(visibility__in=["team", "department"]))
            .order_by("-created_at")[:3]
        )

        # Managers still get a light briefing if they have any memory activity
        is_manager = getattr(user, "role", "") in ("MANAGER", "OWNER", "ADMIN")
        if not reminders and not open_tasks and not list_bits and not recent:
            if not is_manager:
                continue
            # Skip empty manager briefings to avoid noise
            continue

        role_label = "manager" if is_manager else "team"
        lines = [f"☀️ Good morning — your personal Mizan briefing ({restaurant.name}):"]
        if reminders:
            lines.append("Reminders:")
            for r in reminders:
                lines.append(f"• {r.title} ({r.due_at.strftime('%a %H:%M')})")
        if open_tasks:
            lines.append("Open tasks:")
            for t in open_tasks:
                lines.append(f"• {t.title}")
        if list_bits:
            lines.append("Lists:")
            for b in list_bits:
                lines.append(f"• {b}")
        if recent:
            lines.append("Recent memory:")
            for n in recent:
                lines.append(f"• {(n.content or '')[:100]}")
        lines.append(f"\nReply anytime on WhatsApp — I'm your {role_label} memory.")

        try:
            result = notification_service.send_whatsapp_text(phone, "\n".join(lines))
            ok = result[0] if isinstance(result, tuple) else bool(result)
            if ok:
                sent += 1
        except Exception:
            logger.exception("daily_briefing_sweep failed for user=%s", user.id)

    return {"sent": sent}


@shared_task(name="scheduling.memory_tasks.serendipity_sweep")
def serendipity_sweep():
    """
    Occasionally resurface an old team/personal note (Memorae Park).
    Weekly — light touch, not noisy.
    """
    from scheduling.memory_models import MemoryNote
    from notifications.services import notification_service

    cutoff = timezone.now() - timedelta(days=14)
    notes = (
        MemoryNote.objects.filter(
            is_archived=False,
            created_at__lte=cutoff,
            visibility__in=["personal", "team"],
            owner__isnull=False,
        )
        .select_related("owner", "restaurant")
        .order_by("last_recalled_at", "recall_count")[:30]
    )
    sent = 0
    for note in notes:
        phone = re.sub(r"\D", "", str(getattr(note.owner, "phone", "") or note.source_phone or ""))
        if not phone:
            continue
        # Skip if recalled in last 30 days
        if note.last_recalled_at and note.last_recalled_at > timezone.now() - timedelta(days=30):
            continue
        text = f"💭 Remember this?\n{(note.content or '')[:280]}"
        if note.project_key:
            text += f"\n({note.project_key})"
        try:
            result = notification_service.send_whatsapp_text(phone, text)
            ok = result[0] if isinstance(result, tuple) else bool(result)
            if ok:
                note.last_recalled_at = timezone.now()
                note.recall_count = (note.recall_count or 0) + 1
                note.save(update_fields=["last_recalled_at", "recall_count"])
                sent += 1
                if sent >= 20:
                    break
        except Exception:
            logger.exception("serendipity_sweep failed note=%s", note.id)
    return {"sent": sent}
