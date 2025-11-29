from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from scheduling.models import AssignedShift, ShiftTask
from .task_templates import TaskTemplate
import requests, sys
from django.conf import settings
from .utils import get_tasks


def send_whatsapp(phone, message, template_name):
    token = settings.WHATSAPP_ACCESS_TOKEN
    phone_id = settings.WHATSAPP_PHONE_NUMBER_ID
    verision = settings.WHATSAPP_API_VERSION
    url = f"https://graph.facebook.com/v20.0/{phone_id}/messages"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {"code": "en_US"},
                    "components": [
                        {
                            "type": "body",
                            "parameters": message
                        }
                    ]
                }
        }
    response = requests.post(url, json=payload, headers=headers)
    try:
        data = response.json()
    except Exception:
        data = {"error": "Invalid JSON response"}

    # Return both response and parsed JSON to avoid losing info
    return {"status_code": response.status_code, "data": data}


def clock_in_remender(task):
    staff = task.staff
    first_name = staff.first_name
    start_time = task.start_time.strftime('%Y-%m-%d %H:%M')
    duration_from_now = str(int((task.start_time - timezone.now()).total_seconds() // 60)) + " minutes"
    restaurant = task.schedule.restaurant.name
    notification_link = f"https://mizanapp.com/notify_late/{task.id}"
    shift_duration = int((task.end_time - task.start_time).total_seconds() // 60)
    if shift_duration < 60:
        shift_duration = f"{shift_duration} minutes"
    else:
        hours = shift_duration // 60
        minutes = shift_duration % 60
        shift_duration = f"{hours} hours"
        if minutes > 0:
            shift_duration += f" {minutes} minutes"

    if not hasattr(staff, 'phone') or not staff.phone:
        print(f"Staff {staff.id} has no phone number. Skipping reminder.", file=sys.stderr)
        return

    phone = ''.join(filter(str.isdigit, staff.phone))
    message =  [
            {"type": "text", "text": first_name},
            {"type": "text", "text": start_time},
            {"type": "text", "text": duration_from_now},
            {"type": "text", "text": restaurant},
            {"type": "text", "text": 'Unknown'},
            {"type": "text", "text": shift_duration},
    ]

    response = send_whatsapp(phone, message, "clockin_reminder")
    # Handle the case where response is a dict and may not have .status_code
    status_code = response.get("status_code", None)
    data = response.get("data", {})

    print(f"message payload: {message}", file=sys.stderr)
    return status_code  # safe return, always exists now


def send_clock_in_reminder():
    now = timezone.now()
    upcoming_tasks = AssignedShift.objects.filter(
        start_time__gte=now,
        start_time__lte=now + timedelta(minutes=60),
        clock_in_reminder_sent=False
    )

    print(f"Found {upcoming_tasks.count()} upcoming tasks for reminders.", file=sys.stderr)

    for shift in upcoming_tasks:
        if clock_in_remender(shift) == 200:
            shift.clock_in_reminder_sent = True
            shift.save()
            print(f"Marked reminder_sent=True for shift {shift.id}", file=sys.stderr)



def check_list_remender(shift):

    print(f"Preparing checklist reminder for shift {shift.id}", file=sys.stderr)
    staff = shift.staff
    first_name = staff.first_name
    duration_from_now = str(int((shift.start_time - timezone.now()).total_seconds() // 60)) + " minutes"
    restaurant = shift.schedule.restaurant.name
    notification_link = f"https://tst.com"
    tasks = ShiftTask.objects.filter(shift=shift)
    task_templates = shift.task_templates.all()
    # tasktemplate

    key_checklist_items = "test checklist items"
    task_titles = get_tasks(shift=shift, task_templates=task_templates, tasks=tasks)

    print(f"Tasks for shift {shift.id}: {task_titles}", file=sys.stderr)
    if not hasattr(staff, 'phone') or not staff.phone:
        print(f"Staff {staff.id} has no phone number. Skipping reminder.", file=sys.stderr)
        return
    
    phone = ''.join(filter(str.isdigit, staff.phone))
    message = [
            {"type": "text", "text": first_name},
            {"type": "text", "text": 'Title'},
            {"type": "text", "text": 'Special test'},
            {"type": "text", "text": 'Target test'},
            {"type": "text", "text": key_checklist_items},
            {"type": "text", "text": "testlink.com"},
            {"type": "text", "text": task_titles},
        ]
    
    response = send_whatsapp(phone, message, "shift_checklist_preview")
    status_code = response.get("status_code", None)
    data = response.get("data", {})

    print(f"staff {first_name} reminder response: {data}", file=sys.stderr)
    return status_code  # safe return, always exists now


def send_check_list_reminder():
    now = timezone.now()
    upcoming_tasks = AssignedShift.objects.filter(
        start_time__gte=now,
        start_time__lte=now + timedelta(minutes=30),
        check_list_reminder_sent = False
    )

    for shift in upcoming_tasks:
        if check_list_remender(shift) == 200:
            shift.check_list_reminder_sent = True
            shift.save()
            print(f"Marked reminder_sent=True for shift {shift.id}", file=sys.stderr)


@shared_task
def check_upcoming_tasks():
    send_clock_in_reminder()
    send_check_list_reminder()



