"""
Standing Process & Tasks assignments — checklists without a scheduled shift.

Managers assign a TaskTemplate to staff on the process form. When those staff
clock in (or start a checklist while clocked in), we materialize a lightweight
ad-hoc day shift so existing ShiftTask / ShiftChecklistProgress FKs keep working.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

ADHOC_CHECKLIST_MARKER = "[ADHOC_CHECKLIST]"


def get_standing_templates_for_staff(user):
    """Active TaskTemplates assigned to this staff as standing assignees."""
    from scheduling.task_templates import TaskTemplate

    if not user:
        return []
    restaurant = getattr(user, "restaurant", None)
    qs = TaskTemplate.objects.filter(
        is_active=True,
        standing_assignees=user,
    )
    if restaurant:
        qs = qs.filter(restaurant=restaurant)
    return list(qs.distinct())


def attach_standing_templates_to_shift(shift, user) -> int:
    """Add standing templates onto a shift's M2M (idempotent). Returns count added."""
    templates = get_standing_templates_for_staff(user)
    if not templates or not shift:
        return 0
    existing = set(shift.task_templates.values_list("id", flat=True))
    to_add = [t for t in templates if t.id not in existing]
    if to_add:
        shift.task_templates.add(*to_add)
    return len(to_add)


def _week_bounds(day):
    week_start = day - timedelta(days=day.weekday())
    week_end = week_start + timedelta(days=6)
    return week_start, week_end


def _create_adhoc_shift(user, today, templates):
    from scheduling.models import AssignedShift, WeeklySchedule

    restaurant = getattr(user, "restaurant", None)
    if not restaurant:
        return None

    week_start, week_end = _week_bounds(today)
    schedule, _ = WeeklySchedule.objects.get_or_create(
        restaurant=restaurant,
        week_start=week_start,
        defaults={"week_end": week_end},
    )

    start_dt = timezone.make_aware(datetime.combine(today, time(0, 0)))
    end_dt = timezone.make_aware(datetime.combine(today, time(23, 59)))
    role = (getattr(user, "role", None) or "STAFF")[:20]

    shift = AssignedShift.objects.create(
        schedule=schedule,
        staff=user,
        shift_date=today,
        start_time=start_dt,
        end_time=end_dt,
        role=role if role else "STAFF",
        # SCHEDULED until they clock in — avoids looking like a live roster slot
        status="SCHEDULED",
        notes=f"{ADHOC_CHECKLIST_MARKER} Checklist without rostered shift (Processes & Tasks).",
        created_by=user,
    )
    if templates:
        shift.task_templates.add(*templates)
    logger.info(
        "Created ad-hoc checklist shift %s for user %s (%d standing templates)",
        shift.id,
        user.id,
        len(templates),
    )
    return shift


def ensure_checklist_shift_for_staff(user, *, create_adhoc: bool = True):
    """
    Resolve a shift container for WhatsApp checklists.

    Prefer a real scheduled shift for today. Otherwise, if the staff has standing
    process assignments, reuse or create today's ad-hoc checklist shift.

    Returns AssignedShift or None.
    """
    from scheduling.models import AssignedShift

    if not user:
        return None

    today = timezone.localdate()
    now = timezone.now()

    qs = (
        AssignedShift.objects.filter(
            Q(staff=user) | Q(staff_members=user),
            shift_date=today,
            status__in=["SCHEDULED", "CONFIRMED", "IN_PROGRESS"],
        )
        .distinct()
        .select_related("staff")
        .order_by("start_time")
    )

    # Prefer non-ad-hoc scheduled shifts that haven't ended
    real = (
        qs.exclude(notes__contains=ADHOC_CHECKLIST_MARKER)
        .filter(Q(end_time__gt=now) | Q(end_time__isnull=True))
        .order_by("start_time")
        .first()
    )
    if not real:
        real = qs.exclude(notes__contains=ADHOC_CHECKLIST_MARKER).order_by("start_time").first()
    if real:
        attach_standing_templates_to_shift(real, user)
        return real

    templates = get_standing_templates_for_staff(user)
    if not templates:
        # Still allow an existing ad-hoc shift (templates may have been removed)
        adhoc = qs.filter(notes__contains=ADHOC_CHECKLIST_MARKER).first()
        return adhoc

    adhoc = qs.filter(notes__contains=ADHOC_CHECKLIST_MARKER).first()
    if adhoc:
        attach_standing_templates_to_shift(adhoc, user)
        return adhoc

    if not create_adhoc:
        return None

    return _create_adhoc_shift(user, today, templates)


def start_process_for_staff(
    *,
    template,
    staff_users: list,
    created_by=None,
    notify_whatsapp: bool = True,
) -> dict:
    """
    Assign a Process & Tasks template to staff as a Live Board checklist.

    Creates/reuses today's shift container, attaches the template, materializes
    ShiftTasks — never creates dashboard "Tasks & Demands" rows.
    """
    from notifications.services import notification_service
    from scheduling.shift_auto_templates import instantiate_shift_tasks_from_template

    if not template or not staff_users:
        return {"success": False, "error": "template and staff required", "started": []}

    today = timezone.localdate()
    started = []
    tpl_id = str(getattr(template, "id", "") or "")

    for user in staff_users:
        if not user or not getattr(user, "is_active", True):
            continue
        shift = ensure_checklist_shift_for_staff(user, create_adhoc=True)
        if not shift:
            # Force ad-hoc with this template even without standing assignees
            shift = _create_adhoc_shift(user, today, [template])
        if not shift:
            continue

        shift.task_templates.add(template)

        # Skip re-materializing if this template's ShiftTasks already exist for this staff
        already = shift.tasks.filter(
            assigned_to=user,
            branch_config__template_id=tpl_id,
        ).exists()
        created = 0
        if not already:
            created = instantiate_shift_tasks_from_template(
                shift=shift,
                assignee=user,
                task_template=template,
                created_by=created_by,
            )

        notified = False
        if notify_whatsapp:
            phone = "".join(filter(str.isdigit, str(getattr(user, "phone", "") or "")))
            if len(phone) >= 6:
                try:
                    from core.i18n import get_effective_language, tr

                    lang = get_effective_language(
                        user=user, restaurant=getattr(user, "restaurant", None)
                    )
                    notification_service.send_whatsapp_text(
                        phone,
                        tr("process.started.wa", lang, name=template.name),
                    )
                    notified = True
                except Exception:
                    logger.exception("start_process notify failed user=%s", user.pk)

        started.append(
            {
                "staff_id": str(user.id),
                "staff_name": f"{user.first_name or ''} {user.last_name or ''}".strip()
                or user.email,
                "shift_id": str(shift.id),
                "tasks_created": created,
                "already_assigned": already,
                "notified": notified,
            }
        )

    if started:
        try:
            template.usage_count = (template.usage_count or 0) + 1
            template.save(update_fields=["usage_count"])
        except Exception:
            pass

    return {
        "success": True,
        "template_id": str(template.id),
        "template_name": template.name,
        "started": started,
        "count": len(started),
    }
