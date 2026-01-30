
import os
import sys
import uuid
import json
from datetime import timedelta
from django.utils import timezone
from django.test import Client
from django.urls import reverse

# Setup Django environment
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mizan_api.settings')
django.setup()

from accounts.models import CustomUser, Restaurant
from scheduling.models import AssignedShift, WeeklySchedule, ShiftTask, TaskVerificationRecord
from scheduling.task_templates import TaskTemplate
from notifications.models import WhatsAppSession
from timeclock.models import ClockEvent

def run_test():
    print("--- Starting Checklist Verification Run ---")
    client = Client(HTTP_HOST='localhost')
    try:
        webhook_url = reverse('notifications:whatsapp-webhook')
    except:
        webhook_url = "/api/notifications/whatsapp/webhook/"
    print(f"Using Webhook URL: {webhook_url}")
    print("--- 1. Setting up Test Data ---")
    restaurant_name = "Checklist Test Kitchen"
    restaurant = Restaurant.objects.filter(name=restaurant_name).first()
    if not restaurant:
        restaurant = Restaurant.objects.create(
            name=restaurant_name,
            email="checklist_test@mizan.ai", # Unique email
            latitude=51.5074, 
            longitude=-0.1278,
            radius=100,
            language="en"
        )
    else:
        restaurant.latitude = 51.5074
        restaurant.longitude = -0.1278
        restaurant.radius = 100
        restaurant.save()
    
    staff_email = "staff_test@example.com"
    staff, created = CustomUser.objects.get_or_create(
        email=staff_email,
        defaults={
            "first_name": "Test",
            "last_name": "Staff",
            "role": "STAFF",
            "phone": "447700900000",
            "restaurant": restaurant
        }
    )
    if not created:
        staff.phone = "447700900000"
        staff.restaurant = restaurant
        staff.save()

    # Create Template
    tpl, _ = TaskTemplate.objects.get_or_create(
        restaurant=restaurant,
        name="Opening Duties",
        defaults={
            "template_type": "OPENING",
            "tasks": [
                {"title": "Check Fridge Temp", "description": "Ensure below 5C"},
                {"title": "Sanitize Surfaces", "description": "Use blue sprayer"},
                {"title": "Photo of Clean Floor", "description": "Evidence needed", "verification_required": True, "verification_type": "PHOTO"}
            ]
        }
    )

    # Create Shift
    today = timezone.now().date()
    week_start = today - timedelta(days=today.weekday())
    schedule, _ = WeeklySchedule.objects.get_or_create(
        restaurant=restaurant,
        week_start=week_start,
        week_end=week_start + timedelta(days=6)
    )

    shift, _ = AssignedShift.objects.get_or_create(
        staff=staff,
        shift_date=today,
        schedule=schedule,
        defaults={
            "start_time": timezone.now() - timedelta(minutes=5),
            "end_time": timezone.now() + timedelta(hours=4),
            "status": "SCHEDULED",
            "role": "STAFF"
        }
    )
    shift.status = 'SCHEDULED'
    shift.start_time = timezone.now() - timedelta(minutes=5)
    shift.end_time = timezone.now() + timedelta(hours=4)
    shift.save()
    shift.task_templates.add(tpl)
    
    phone = staff.phone # Moved this line up

    # Clear existing session and events
    WhatsAppSession.objects.filter(phone=phone).delete()
    ClockEvent.objects.filter(staff=staff).delete()
    ShiftTask.objects.filter(shift=shift).delete() # Retained this line
    
    # Create a fresh one
    session = WhatsAppSession.objects.create(phone=phone, user=staff, state='idle')

    print(f"Test User: {staff.email} ({phone})")
    print(f"Shift ID: {shift.id}")

    # 2. Simulate Clock In
    print("\n--- 2. Simulating Clock In Command ---")
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": phone,
                        "type": "text",
                        "text": {"body": "clock in"}
                    }]
                },
                "field": "messages"
            }]
        }]
    }
    response = client.post(webhook_url, data=payload, content_type="application/json")
    print(f"Clock In Response: {response.status_code}")
    if response.status_code != 200:
        print(f"Error Content: {response.content}")
    
    session = WhatsAppSession.objects.get(phone=phone)
    print(f"Session State: {session.state}") # Should be awaiting_clock_in_location

    # 3. Simulate Location Submission (Success)
    print("\n--- 3. Simulating Location Submission (Within Geofence) ---")
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": phone,
                        "type": "location",
                        "location": {
                            "latitude": 51.5075, # Slightly off but within 100m
                            "longitude": -0.1279
                        }
                    }]
                },
                "field": "messages"
            }]
        }]
    }
    response = client.post(webhook_url, data=payload, content_type="application/json")
    print(f"Location Response: {response.status_code}")
    if response.status_code != 200:
        print(f"Error Content: {response.content}")
    
    session.refresh_from_db()
    print(f"Session State after Location: {session.state}") # Should be in_checklist or awaiting_task_photo
    
    tasks = ShiftTask.objects.filter(shift=shift).order_by('created_at')
    print(f"Generated Tasks: {tasks.count()}")
    for t in tasks:
        print(f"  - [{t.status}] {t.title} (Verification: {t.verification_type})")

    # 4. Progress Checklist (Task 1: Yes)
    print("\n--- 4. Completing Task 1 (Button Click) ---")
    session.refresh_from_db()
    
    if 'checklist' not in session.context:
        print(f"ERROR: 'checklist' not in session.context. State: {session.state}")
        return

    first_task_id = session.context['checklist']['current_task_id']
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": phone,
                        "type": "interactive",
                        "interactive": {
                            "type": "button_reply",
                            "button_reply": {"id": "yes", "title": "✅ Yes"}
                        }
                    }]
                },
                "field": "messages"
            }]
        }]
    }
    response = client.post(webhook_url, data=payload, content_type="application/json")
    print(f"Task 1 Yes Response: {response.status_code}")
    if response.status_code != 200:
        print(f"Error Content: {response.content}")
    
    task1 = ShiftTask.objects.get(id=first_task_id)
    print(f"Task 1 Status: {task1.status}") # Should be COMPLETED

    # 5. Progress Checklist (Task 2: No -> Help)
    print("\n--- 5. Handling 'No' followed by 'Need Help' ---")
    session.refresh_from_db()
    second_task_id = session.context['checklist']['current_task_id']
    
    # Click No
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["interactive"]["button_reply"]["id"] = "no"
    response = client.post(webhook_url, data=payload, content_type="application/json")
    print(f"Task 2 No Response: {response.status_code}")
    if response.status_code != 200:
        print(f"Error Content: {response.content}")
    
    session.refresh_from_db()
    print(f"Session State after 'No': {session.state}") # Should be checklist_followup
    
    # Click Need Help
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["interactive"]["button_reply"]["id"] = "need_help"
    response = client.post(webhook_url, data=payload, content_type="application/json")
    print(f"Task 2 Help Response: {response.status_code}")
    if response.status_code != 200:
        print(f"Error Content: {response.content}")
    
    session.refresh_from_db()
    print(f"Session State after 'Need Help': {session.state}") # Should be checklist_help_text
    
    # Send help text
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["type"] = "text"
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["text"] = {"body": "The blue sprayer is empty."}
    response = client.post(webhook_url, data=payload, content_type="application/json")
    print(f"Help Text Response: {response.status_code}")
    if response.status_code != 200:
        print(f"Error Content: {response.content}")
    
    task2 = ShiftTask.objects.get(id=second_task_id)
    print(f"Task 2 Notes:\n{task2.notes}")
    session.refresh_from_db()
    print(f"Session State after Help Text: {session.state}") # Should resume checklist

    # --- 5.5 Complete Task 2 (Yes) ---
    print("\n--- 5.5 Completing Task 2 (Yes) ---")
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["type"] = "interactive"
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["interactive"] = {
        "type": "button_reply",
        "button_reply": {"id": "yes", "title": "✅ Yes"}
    }
    response = client.post(webhook_url, data=payload, content_type="application/json")
    print(f"Task 2 Yes Response: {response.status_code}")
    
    task2.refresh_from_db()
    print(f"Task 2 Status: {task2.status}") # Should be COMPLETED

    # 6. Photo Verification
    print("\n--- 6. Simulating Photo Verification ---")
    session.refresh_from_db()
    photo_task_id = session.context['checklist']['current_task_id']
    print(f"Current Task ID: {photo_task_id}")
    print(f"Awaiting Verification For: {session.context.get('awaiting_verification_for_task_id')}")
    print(f"Session State: {session.state}") # Should be awaiting_task_photo
    
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": phone,
                        "type": "image",
                        "image": {
                            "id": "media_id_123",
                            "mime_type": "image/jpeg",
                            "caption": "All clean!"
                        }
                    }]
                },
                "field": "messages"
            }]
        }]
    }
    response = client.post(webhook_url, data=payload, content_type="application/json")
    print(f"Photo Response: {response.status_code}")
    if response.status_code != 200:
        print(f"Error Content: {response.content}")
    
    photo_task = ShiftTask.objects.get(id=photo_task_id)
    print(f"Photo Task Status: {photo_task.status}")
    from scheduling.models import TaskVerificationRecord
    record = TaskVerificationRecord.objects.get(task=photo_task)
    print(f"Verification Evidence: {record.photo_evidence}")

    print("\n--- Verification Complete ---")

if __name__ == "__main__":
    run_test()
