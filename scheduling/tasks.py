from celery import shared_task
from django.utils import timezone
from django.db import transaction
from datetime import timedelta
from scheduling.models import AssignedShift, ShiftTask, ShiftChecklistProgress
from timeclock.models import ClockEvent
from .task_templates import TaskTemplate
from .reminder_tasks import _shift_recipients
from scheduling.audit import AuditTrailService, AuditActionType, AuditSeverity
import requests, sys
from django.conf import settings
from .utils import get_tasks
from notifications.services import notification_service




def shift_reminder(task):
    """
    Shift Reminder (30 minutes before shift start).
    Purpose: remind staff of upcoming shift (NOT a clock-in CTA).
    Uses WhatsApp template: `clock_in_reminder` (per your template name).

    Template parameters (per WhatsApp template preview):
    {{1}} = staff first name
    {{2}} = time until shift starts (e.g., "30 minutes")
    {{3}} = location (restaurant address or name)
    {{4}} = shift details (title + start time + role)
    {{5}} = duration (e.g., "5h 0m")
    """
    try:
        staff = task.staff
        first_name = staff.first_name or "Team Member"
        restaurant = getattr(getattr(task, 'schedule', None), 'restaurant', None)

        now = timezone.now()
        shift_start = getattr(task, 'start_time', None)
        shift_end = getattr(task, 'end_time', None)
        if shift_start:
            try:
                shift_start = timezone.localtime(shift_start)
            except Exception:
                pass
        if shift_end:
            try:
                shift_end = timezone.localtime(shift_end)
            except Exception:
                pass

        minutes_until = 0
        if shift_start:
            minutes_until = int(max(0, (shift_start - now).total_seconds() // 60))
        minutes_from_now = f"{minutes_until} minutes"

        location = getattr(restaurant, 'address', None) or getattr(restaurant, 'name', None) or "Restaurant"
        # Add start time + role into the "Shift" field so the reminder includes key details
        start_str = shift_start.strftime('%I:%M %p').lstrip('0') if hasattr(shift_start, 'strftime') else ''
        role = (getattr(task, 'role', '') or '').upper() or 'STAFF'
        base_title = (getattr(task, 'notes', '') or '').strip() or "Shift"
        shift_details = f"{base_title} • {start_str} • {role}".strip(" •")

        duration_text = ""
        if shift_start and shift_end:
            dur = shift_end - shift_start
            if dur.total_seconds() < 0:
                # Overnight safety
                dur = dur + timedelta(days=1)
            mins = int(dur.total_seconds() // 60)
            duration_text = f"{mins // 60}h {mins % 60}m"
        else:
            duration_text = "—"
        
    except Exception as e:
        print(f"Error preparing reminder for shift {task.id}: {e}", file=sys.stderr)
        return None

    if not hasattr(staff, 'phone') or not staff.phone:
        return None

    # Send shift reminder template (no clock-in CTA)
    components = [
        {
            "type": "body",
            "parameters": [
                {"type": "text", "text": first_name},
                {"type": "text", "text": minutes_from_now},
                {"type": "text", "text": str(location)},
                {"type": "text", "text": shift_details},
                {"type": "text", "text": duration_text},
            ]
        }
    ]
    
    ok, _ = notification_service.send_whatsapp_template(
        phone=staff.phone,
        template_name='clock_in_reminder',
        language_code='en_US',
        components=components
    )
    return 200 if ok else 400


def send_shift_reminder_30min():
    now = timezone.now()
    # Check shifts starting between 25 and 35 minutes from now (targeting ~30 min)
    upcoming_tasks = AssignedShift.objects.filter(
        start_time__gte=now + timedelta(minutes=25),
        start_time__lte=now + timedelta(minutes=35),
        shift_reminder_sent=False,
        status__in=['SCHEDULED', 'CONFIRMED']
    )

    print(f"Found {upcoming_tasks.count()} upcoming shifts for 30-min reminders.", file=sys.stderr)

    for shift in upcoming_tasks:
        if shift_reminder(shift) == 200:
            shift.shift_reminder_sent = True
            shift.save(update_fields=['shift_reminder_sent'])
            print(f"Marked shift_reminder_sent=True for shift {shift.id}", file=sys.stderr)


def clock_in_reminder(shift, recipient):
    """
    Send clock-in reminder to one recipient. Used by send_clock_in_reminder_10min.
    Delegates to NotificationService so Miya (Lua) sends the reminder when configured.
    """
    try:
        if not getattr(recipient, 'phone', None) or not recipient.phone:
            return None
        ok = notification_service.send_shift_notification(
            shift, notification_type='CLOCK_IN_REMINDER', recipient=recipient
        )
        return 200 if ok else 400
    except Exception as e:
        print(f"Error sending clock-in reminder for shift {shift.id} to {recipient}: {e}", file=sys.stderr)
        return None


def send_clock_in_reminder_10min():
    now = timezone.now()
    # Check shifts starting between 5 and 15 minutes from now (targeting ~10 min); only today's shifts
    upcoming_tasks = AssignedShift.objects.filter(
        shift_date=now.date(),
        start_time__gte=now + timedelta(minutes=5),
        start_time__lte=now + timedelta(minutes=15),
        clock_in_reminder_sent=False,
        status__in=['SCHEDULED', 'CONFIRMED']
    ).select_related('staff', 'schedule__restaurant').prefetch_related('staff_members')

    print(f"Found {upcoming_tasks.count()} upcoming shifts for 10-min clock-in reminders.", file=sys.stderr)

    for shift in upcoming_tasks:
        sent_any = False
        for member in _shift_recipients(shift):
            if clock_in_reminder(shift, member) == 200:
                sent_any = True
                print(f"Sent clock-in reminder to {getattr(member, 'email', member)} for shift {shift.id}", file=sys.stderr)
        if sent_any:
            shift.clock_in_reminder_sent = True
            shift.save(update_fields=['clock_in_reminder_sent'])
            print(f"Marked clock_in_reminder_sent=True for shift {shift.id}", file=sys.stderr)


def clock_out_reminder(task):
    """
    Send a WhatsApp reminder to clock out using the clock_out_reminder template.
    Template has no parameters - just a "Clock-Out" button.
    """
    try:
        staff = task.staff
    except Exception as e:
        return None
        
    if not hasattr(staff, 'phone') or not staff.phone:
        return None

    # Use clock_out_reminder template (no body parameters)
    ok, _ = notification_service.send_whatsapp_template(
        phone=staff.phone,
        template_name='clock_out_reminder',
        language_code='en_US',
        components=[]
    )
    return 200 if ok else 400


def send_clock_out_reminder():
    now = timezone.now()
    # Check shifts ending now (or recently)
    # Give a 15 min buffer to send reminder
    ending_tasks = AssignedShift.objects.filter(
        end_time__gte=now - timedelta(minutes=15),
        end_time__lte=now + timedelta(minutes=5),
        clock_out_reminder_sent=False,
        status='IN_PROGRESS'
    )
    
    for shift in ending_tasks:
        if clock_out_reminder(shift) == 200:
            shift.clock_out_reminder_sent = True
            shift.save(update_fields=['clock_out_reminder_sent'])
            print(f"Marked clock_out_reminder_sent=True for shift {shift.id}", file=sys.stderr)


def auto_clock_out_after_shift_end():
    """
    Automatic clock-out when scheduled shift end time is reached (Miya/system-level).
    For restaurants with automatic_clock_out=True:
    - Creates clock-out event for staff still clocked in (idempotent).
    - Updates shift status to COMPLETED.
    - Marks in-progress checklists as INCOMPLETE_SHIFT_END and logs incomplete tasks.
    - Sends optional notification to staff; logs all actions for audit.
    Runs as soon as shift end time has passed (no grace period) so staff are clocked out at shift end.
    """
    now = timezone.now()
    ended_shifts = AssignedShift.objects.filter(
        end_time__lte=now,
        status__in=['IN_PROGRESS', 'SCHEDULED', 'CONFIRMED'],
    ).select_related('schedule__restaurant').prefetch_related('staff_members')

    count = 0
    for shift in ended_shifts:
        restaurant = getattr(getattr(shift, 'schedule', None), 'restaurant', None)
        if not restaurant or not getattr(restaurant, 'automatic_clock_out', False):
            continue

        shift_clocked_out_any = False
        for user in _shift_recipients(shift):
            last_event = ClockEvent.objects.filter(staff=user).order_by('-timestamp').first()
            if not last_event or last_event.event_type != 'in':
                continue

            try:
                with transaction.atomic():
                    # 1. Record clock-out (idempotent: we only run when last is 'in')
                    ClockEvent.objects.create(
                        staff=user,
                        event_type='out',
                        device_id='auto_clock_out',
                        notes='Auto clock-out at shift end',
                    )
                    shift_clocked_out_any = True
                    count += 1

                    # 2. Calculate total hours worked (this shift)
                    duration_seconds = (now - last_event.timestamp).total_seconds()
                    hours_worked = round(duration_seconds / 3600, 2)

                    # 3. Checklist: if in progress or not completed, mark incomplete and log
                    prog = ShiftChecklistProgress.objects.filter(shift=shift, staff=user).first()
                    completion_pct = 100
                    incomplete_task_ids = []
                    if prog and prog.status == 'IN_PROGRESS':
                        task_ids = prog.task_ids or []
                        responses = prog.responses or {}
                        completed_count = sum(1 for tid in task_ids if tid in responses)
                        total = len(task_ids)
                        completion_pct = int((completed_count / total) * 100) if total else 100
                        incomplete_task_ids = [tid for tid in task_ids if tid not in responses]
                        prog.status = 'INCOMPLETE_SHIFT_END'
                        prog.updated_at = now
                        prog.save(update_fields=['status', 'updated_at'])

                    # 4. Audit log
                    try:
                        AuditTrailService.log_activity(
                            user=user,
                            action=AuditActionType.AUTO_CLOCK_OUT,
                            description=f"Auto clock-out at shift end (shift {shift.id}); worked {hours_worked}h; checklist {completion_pct}%",
                            content_object=shift,
                            new_values={
                                'shift_id': str(shift.id),
                                'hours_worked': hours_worked,
                                'checklist_completion_pct': completion_pct,
                                'incomplete_task_ids': incomplete_task_ids,
                                'device_id': 'auto_clock_out',
                            },
                            severity=AuditSeverity.MEDIUM,
                            metadata={'source': 'auto_clock_out_after_shift_end'},
                        )
                    except Exception:
                        pass

                # 5. Optional notification (outside atomic so failure doesn't rollback)
                if getattr(user, 'phone', None):
                    try:
                        msg = (
                            "Your shift has ended and you have been automatically clocked out. "
                            "See you next shift!"
                        )
                        notification_service.send_whatsapp_text(user.phone, msg)
                    except Exception:
                        pass

                print(f"Auto clock-out for {getattr(user, 'email', user.id)} (shift {shift.id})", file=sys.stderr)
            except Exception as e:
                print(f"Auto clock-out failed for {user.id}: {e}", file=sys.stderr)

        # 6. Update shift status to COMPLETED (shift end time passed; ensures accurate records)
        try:
            with transaction.atomic():
                AssignedShift.objects.filter(pk=shift.pk).update(status='COMPLETED')
            if shift_clocked_out_any:
                print(f"Shift {shift.id} marked COMPLETED after auto clock-out.", file=sys.stderr)
        except Exception as e:
            print(f"Failed to update shift status for {shift.id}: {e}", file=sys.stderr)

    if count:
        print(f"Auto clock-out: {count} staff clocked out.", file=sys.stderr)
    return count


def check_list_reminder(shift):
    print(f"Preparing checklist reminder for shift {shift.id}", file=sys.stderr)
    staff = shift.staff
    first_name = staff.first_name
    
    tasks = ShiftTask.objects.filter(shift=shift)
    # Simplified logic
    task_titles = ", ".join([t.title for t in tasks[:3]])
    if len(tasks) > 3:
        task_titles += "..."

    if not hasattr(staff, 'phone') or not staff.phone:
        return None
    
    message = (
        f"Hi {first_name}! 📋 You have {tasks.count()} tasks assigned for your shift.\n\n"
        f"Preview: {task_titles}\n\n"
        "Good luck!"
    )
    
    ok, _ = notification_service.send_whatsapp_text(staff.phone, message)
    return 200 if ok else 400


def send_check_list_reminder():
    now = timezone.now()
    # Logic for checklist reminder (e.g., at start of shift)
    active_shifts = AssignedShift.objects.filter(
        start_time__lte=now,
        end_time__gt=now,
        check_list_reminder_sent=False,
        status__in=['IN_PROGRESS', 'CONFIRMED']
    )

    for shift in active_shifts:
        if check_list_reminder(shift) == 200:
            shift.check_list_reminder_sent = True
            shift.save(update_fields=['check_list_reminder_sent'])


@shared_task
def check_upcoming_tasks():
    send_shift_reminder_30min()
    send_clock_in_reminder_10min()
    send_check_list_reminder()
    send_clock_out_reminder()
    auto_clock_out_after_shift_end()



