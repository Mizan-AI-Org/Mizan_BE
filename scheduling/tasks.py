from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from scheduling.models import AssignedShift, ShiftTask
from .task_templates import TaskTemplate
import requests, sys
from django.conf import settings
from .utils import get_tasks
from notifications.services import notification_service
from core.i18n import get_effective_language, whatsapp_language_code, tr




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
        lang = get_effective_language(user=staff, restaurant=restaurant)

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
        minutes_from_now = tr("time.minutes_from_now", lang, n=minutes_until)

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
        language_code=whatsapp_language_code(lang),
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


def clock_in_reminder(task):
    """
    Clock-In Reminder (10 minutes before shift start).
    Purpose: prompt staff to actively clock in.
    Uses WhatsApp template: `staff_clock_in` (contains the Clock-In CTA button).

    Expected template parameters (per your template preview):
    {{1}} = staff first name
    {{2}} = shift start time (e.g., "14:00")
    {{3}} = minutes from now (e.g., "10 minutes")
    {{4}} = location (restaurant address or name)
    """
    try:
        staff = task.staff
        first_name = staff.first_name or "Team Member"
        restaurant = getattr(getattr(task, 'schedule', None), 'restaurant', None)
        lang = get_effective_language(user=staff, restaurant=restaurant)

        now = timezone.now()
        shift_start = getattr(task, 'start_time', None)
        if shift_start:
            try:
                shift_start = timezone.localtime(shift_start)
            except Exception:
                pass

        start_time = shift_start.strftime('%H:%M') if hasattr(shift_start, 'strftime') else ''
        minutes_until = 0
        if shift_start:
            minutes_until = int(max(0, (shift_start - now).total_seconds() // 60))
        minutes_from_now = tr("time.minutes_from_now", lang, n=minutes_until)

        location = getattr(restaurant, 'address', None) or getattr(restaurant, 'name', None) or "Restaurant"
    except Exception as e:
        print(f"Error preparing clock-in reminder for shift {task.id}: {e}", file=sys.stderr)
        return None

    if not hasattr(staff, 'phone') or not staff.phone:
        return None

    components = [
        {
            "type": "body",
            "parameters": [
                {"type": "text", "text": first_name},
                {"type": "text", "text": start_time},
                {"type": "text", "text": minutes_from_now},
                {"type": "text", "text": str(location)},
            ],
        }
    ]

    ok, _ = notification_service.send_whatsapp_template(
        phone=staff.phone,
        template_name='staff_clock_in',
        language_code=whatsapp_language_code(lang),
        components=components
    )
    return 200 if ok else 400


def send_clock_in_reminder_10min():
    now = timezone.now()
    # Check shifts starting between 5 and 15 minutes from now (targeting ~10 min)
    upcoming_tasks = AssignedShift.objects.filter(
        start_time__gte=now + timedelta(minutes=5),
        start_time__lte=now + timedelta(minutes=15),
        clock_in_reminder_sent=False,
        status__in=['SCHEDULED', 'CONFIRMED']
    )

    print(f"Found {upcoming_tasks.count()} upcoming shifts for 10-min clock-in reminders.", file=sys.stderr)

    for shift in upcoming_tasks:
        if clock_in_reminder(shift) == 200:
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
    restaurant = getattr(getattr(task, 'schedule', None), 'restaurant', None)
    lang = get_effective_language(user=staff, restaurant=restaurant)
    ok, _ = notification_service.send_whatsapp_template(
        phone=staff.phone,
        template_name='clock_out_reminder',
        language_code=whatsapp_language_code(lang),
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


def check_list_reminder(shift):
    print(f"Preparing checklist reminder for shift {shift.id}", file=sys.stderr)
    staff = shift.staff
    first_name = staff.first_name
    restaurant = getattr(getattr(shift, 'schedule', None), 'restaurant', None)
    lang = get_effective_language(user=staff, restaurant=restaurant)
    
    tasks = ShiftTask.objects.filter(shift=shift)
    # Simplified logic
    task_titles = ", ".join([t.title for t in tasks[:3]])
    if len(tasks) > 3:
        task_titles += tr("checklist.preview.more", lang)

    if not hasattr(staff, 'phone') or not staff.phone:
        return None
    
    message = tr(
        "checklist.reminder",
        lang,
        name=first_name or "Team Member",
        count=tasks.count(),
        preview=task_titles or "—",
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



