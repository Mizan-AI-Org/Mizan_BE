

import requests
from django.conf import settings

def shift_create_notification( shift_instance):
    try:
            phone = shift_instance.staff.phone
            name = shift_instance.staff.first_name
            phone = ''.join(filter(str.isdigit, phone))
            start_week = shift_instance.schedule.week_start.strftime('%Y-%m-%d')
            end_week = shift_instance.schedule.week_end.strftime('%Y-%m-%d')
            # total_hours = sum(shift_instance.get_shift_duration_hours() for shift in shift_instance.schedule.assigned_shifts.filter(staff=shift_instance.staff))
            total_hours = shift_instance.get_shift_duration_hours()
            total_hours_tf = f" {int(total_hours )}h {int((total_hours - int(total_hours)) * 60)}m"
            next_shift_date = shift_instance.shift_date.strftime('%Y-%m-%d')
            next_shift_time = shift_instance.start_time.strftime('%H:%M') if shift_instance.start_time else ''
            next_shift_end_time = shift_instance.end_time.strftime('%H:%M') if shift_instance.end_time else ''
            message = [
                {"type": "text", "text": name},
                {"type": "text", "text": f"{start_week} to {end_week}"},
                {"type": "text", "text": f"{shift_instance.role} on {next_shift_date} at {next_shift_time} to {next_shift_end_time}"},
                {"type": "text", "text": total_hours_tf},
                {"type": "text", "text": next_shift_date},
                {"type": "text", "text": next_shift_time}
            ]
            print(f"Prepared message: {message}", flush=True, file=sys.stderr)
            resp_code = send_whatsapp(phone, message, "schedule_publication_reminder", 'en')
            return  resp_code['status_code']
    except Exception as e:
        print(f"Error: {e}", flush=True, file=sys.stderr)
        start_week = "N/A"

# def check_if_shift_changed(old_shift, new_shift):


def send_whatsapp(phone, message, template_name, language_code="en_US"):
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
                    "language": {"code": language_code},
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



import sys

def get_tasks(max_tasks=3, shift=None, task_templates=[], tasks=[]):
    print("Getting tasks for shift:", file=sys.stderr)
    print(shift.id, file=sys.stderr)
    print(task_templates, file=sys.stderr)
    print(tasks, file=sys.stderr)

    task_titles = ""
    count = 0

    # Take from task_templates first
    for template in task_templates:
        if count >= max_tasks:
            break
        task_titles += f"Task {count + 1}: {template.name} | "
        count += 1

    # Then from tasks
    for task in tasks:
        if count >= max_tasks:
            break
        task_titles += f"Task {count + 1}: {task.title} | "
        count += 1

    # If no tasks found
    if count == 0:
        return "No tasks assigned for this shift."

    return task_titles   
