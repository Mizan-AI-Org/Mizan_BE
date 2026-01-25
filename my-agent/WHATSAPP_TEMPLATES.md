# WhatsApp Templates Setup Guide

This document explains how to create and use WhatsApp templates via Lua API for Mizan AI.

## Templates Created

1. **staff_invitation** - Welcome message with registration link
2. **weekly_schedule_published** - Weekly schedule notification
3. **shift_checklist_reminder** - Checklist 1 hour before shift
4. **shift_reminder_30min** - Shift reminder 30 minutes before
5. **clock_in_reminder** - Clock-in reminder 10 minutes before
6. **shift_updated** - Shift update notifications
7. **checklist_updated** - Checklist update notifications
8. **clock_in_flow** - Interactive clock-in with location
9. **clock_out_flow** - Interactive clock-out
10. **voice_incident_report** - Voice incident reporting guide

## Setup

### 1. Install Dependencies
```bash
cd my-agent
npm install axios
```

### 2. Set Environment Variables
```bash
export LUA_API_URL="https://api.heylua.ai"
export LUA_WEBHOOK_API_KEY="your_lua_api_key"
```

### 3. Create Templates
```bash
npm run create-templates
```

Or directly:
```bash
cd my-agent
npx ts-node src/templates/whatsapp-templates.ts
```

## Usage in Notification Service

### Update Django `notifications/services.py`

```python
def send_shift_notification(self, shift, notification_type='SHIFT_ASSIGNED'):
    """Send shift notification using Lua templates"""
    
    template_map = {
        'SHIFT_ASSIGNED': 'weekly_schedule_published',
        'SHIFT_UPDATED': 'shift_updated',
        'SHIFT_REMINDER': 'shift_reminder_30min',
        'CLOCK_IN_REMINDER': 'clock_in_reminder',
        'CHECKLIST_REMINDER': 'shift_checklist_reminder',
    }
    
    template_name = template_map.get(notification_type)
    
    # Use Lua template instead of custom message
    return self.send_whatsapp_template(
        to_phone=shift.staff.phone,
        template_name=template_name,
        parameters=[
            shift.staff.first_name,
            shift.shift_date.strftime('%B %d, %Y'),
            f"{shift.start_time.strftime('%H:%M')} - {shift.end_time.strftime('%H:%M')}"
        ]
    )
```

## Template Parameters

### staff_invitation
- {{1}}: Restaurant name
- {{2}}: Staff first name
- {{3}}: Restaurant name (repeated)
- {{1}} (URL): Registration link

### weekly_schedule_published
- {{1}}: Week start date
- {{2}}: Staff first name
- {{3}}: Week start date (formatted)
- {{4}}: Total shifts count
- {{5}}: Total hours
- {{1}} (URL): Schedule URL

### shift_reminder_30min
- {{1}}: Staff first name
- {{2}}: Role (e.g., "Waiter")
- {{3}}: Start time
- {{4}}: Restaurant location
- {{5}}: Shift duration

### clock_in_flow
- {{1}}: Staff first name

### shift_updated
- {{1}}: Staff first name
- {{2}}: Shift date
- {{3}}: New time
- {{4}}: Changes description
- {{1}} (URL): Details URL

## Automated Triggers

### Celery Tasks (Add to `scheduling/tasks.py`)

```python
from celery import shared_task
from datetime import timedelta
from django.utils import timezone
from scheduling.models import AssignedShift
from notifications.services import NotificationService

@shared_task
def send_shift_reminders_30min():
    """Send 30-minute reminders"""
    now = timezone.now()
    upcoming_shifts = AssignedShift.objects.filter(
        start_time__gte=now,
        start_time__lte=now + timedelta(minutes=35),
        shift_date=now.date()
    )
    
    service = NotificationService()
    for shift in upcoming_shifts:
        service.send_shift_notification(shift, 'SHIFT_REMINDER')

@shared_task
def send_checklist_reminders():
    """Send checklist reminders 1 hour before"""
    now = timezone.now()
    upcoming_shifts = AssignedShift.objects.filter(
        start_time__gte=now + timedelta(minutes=55),
        start_time__lte=now + timedelta(minutes=65),
        shift_date=now.date()
    ).exclude(check_list_reminder_sent=True)
    
    service = NotificationService()
    for shift in upcoming_shifts:
        service.send_shift_notification(shift, 'CHECKLIST_REMINDER')
        shift.check_list_reminder_sent = True
        shift.save()

@shared_task
def send_clock_in_reminders():
    """Send clock-in reminders 10 minutes before"""
    now = timezone.now()
    upcoming_shifts = AssignedShift.objects.filter(
        start_time__gte=now + timedelta(minutes=5),
        start_time__lte=now + timedelta(minutes=15),
        shift_date=now.date()
    ).exclude(clock_in_reminder_sent=True)
    
    service = NotificationService()
    for shift in upcoming_shifts:
        service.send_shift_notification(shift, 'CLOCK_IN_REMINDER')
        shift.clock_in_reminder_sent = True
        shift.save()
```

### Celery Beat Schedule (Add to `settings.py`)

```python
CELERY_BEAT_SCHEDULE = {
    'shift-reminders-30min': {
        'task': 'scheduling.tasks.send_shift_reminders_30min',
        'schedule': crontab(minute='*/5'),  # Every 5 minutes
    },
    'checklist-reminders': {
        'task': 'scheduling.tasks.send_checklist_reminders',
        'schedule': crontab(minute='*/10'),  # Every 10 minutes
    },
    'clock-in-reminders': {
        'task': 'scheduling.tasks.send_clock_in_reminders',
        'schedule': crontab(minute='*/5'),  # Every 5 minutes
    },
}
```

## Testing

```bash
# Create templates
npm run create-templates

# Test in Django shell
python manage.py shell

from scheduling.models import AssignedShift
from notifications.services import NotificationService

shift = AssignedShift.objects.first()
service = NotificationService()
service.send_shift_notification(shift, 'SHIFT_REMINDER')
```

## Template IDs

After creation, Lua will return template IDs. Store these in your `.env`:

```
WHATSAPP_TEMPLATE_STAFF_INVITATION=template_id_here
WHATSAPP_TEMPLATE_SCHEDULE_PUBLISHED=template_id_here
WHATSAPP_TEMPLATE_SHIFT_CHECKLIST=template_id_here
# ... etc
```
