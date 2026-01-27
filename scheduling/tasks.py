from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from scheduling.models import AssignedShift, ShiftTask
from .task_templates import TaskTemplate
import requests, sys
from django.conf import settings
from .utils import get_tasks
from notifications.services import notification_service




def clock_in_reminder(task):
    """
    Send a WhatsApp clock-in reminder using the staff_clock_in template.
    Triggered 10 minutes before shift start.
    
    Template parameters:
    {{1}} = staff first name
    {{2}} = shift start time (e.g., "14:00")
    {{3}} = minutes from now (e.g., "10 minutes")
    {{4}} = restaurant location/name
    """
    try:
        staff = task.staff
        first_name = staff.first_name or "Team Member"
        start_time = task.start_time.strftime('%H:%M')
        restaurant = task.schedule.restaurant.name
        
        # Calculate minutes until shift
        now = timezone.now()
        shift_start = timezone.datetime.combine(task.shift_date, task.start_time)
        if timezone.is_naive(shift_start):
            shift_start = timezone.make_aware(shift_start)
        
        minutes_until = int((shift_start - now).total_seconds() / 60)
        if minutes_until < 0:
            minutes_until = 0
        minutes_from_now = f"{minutes_until} minutes"
        
    except Exception as e:
        print(f"Error preparing reminder for shift {task.id}: {e}", file=sys.stderr)
        return None

    if not hasattr(staff, 'phone') or not staff.phone:
        return None

    # Use staff_clock_in template
    components = [
        {
            "type": "body",
            "parameters": [
                {"type": "text", "text": first_name},
                {"type": "text", "text": start_time},
                {"type": "text", "text": minutes_from_now},
                {"type": "text", "text": restaurant}
            ]
        }
    ]
    
    ok, _ = notification_service.send_whatsapp_template(
        phone=staff.phone,
        template_name='staff_clock_in',
        language_code='en_US',
        components=components
    )
    return 200 if ok else 400

def send_clock_in_reminder():
    now = timezone.now()
    # Check shifts starting between 5 and 15 minutes from now (targeting ~10 min)
    upcoming_tasks = AssignedShift.objects.filter(
        start_time__gte=now + timedelta(minutes=5),
        start_time__lte=now + timedelta(minutes=15),
        clock_in_reminder_sent=False,
        status='SCHEDULED'
    )

    print(f"Found {upcoming_tasks.count()} upcoming tasks for reminders.", file=sys.stderr)

    for shift in upcoming_tasks:
        if clock_in_reminder(shift) == 200:
            shift.clock_in_reminder_sent = True
            shift.save(update_fields=['clock_in_reminder_sent'])
            print(f"Marked reminder_sent=True for shift {shift.id}", file=sys.stderr)


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
    send_clock_in_reminder()
    send_check_list_reminder()
    send_clock_out_reminder()



