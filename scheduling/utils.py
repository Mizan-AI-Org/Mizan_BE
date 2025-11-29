

import requests
from django.conf import settings


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
