"""
Celery tasks for automated shift reminders and notifications
"""
from celery import shared_task
from datetime import timedelta
from django.utils import timezone
from scheduling.models import AssignedShift
from notifications.services import NotificationService
import logging

logger = logging.getLogger(__name__)


@shared_task
def send_shift_reminders_30min():
    """
    Send 30-minute shift reminders to staff
    Runs every 5 minutes via Celery Beat
    """
    now = timezone.now()
    upcoming_shifts = AssignedShift.objects.filter(
        start_time__gte=now + timedelta(minutes=25),
        start_time__lte=now + timedelta(minutes=35),
        shift_date=now.date(),
        shift_reminder_sent=False
    ).select_related('staff', 'schedule__restaurant')
    
    service = NotificationService()
    count = 0
    
    for shift in upcoming_shifts:
        if shift.staff.phone:
            try:
                service.send_shift_notification(shift, notification_type='SHIFT_REMINDER')
                shift.shift_reminder_sent = True
                shift.save(update_fields=['shift_reminder_sent'])
                count += 1
                logger.info(f"Sent 30-min reminder to {shift.staff.email} for shift {shift.id}")
            except Exception as e:
                logger.error(f"Failed to send 30-min reminder for shift {shift.id}: {e}")
    
    logger.info(f"Sent {count} 30-minute shift reminders")
    return f"Sent {count} reminders"


@shared_task
def send_checklist_reminders():
    """
    Send checklist reminders 1 hour before shift
    Runs every 10 minutes via Celery Beat
    """
    now = timezone.now()
    upcoming_shifts = AssignedShift.objects.filter(
        start_time__gte=now + timedelta(minutes=55),
        start_time__lte=now + timedelta(minutes=65),
        shift_date=now.date(),
        check_list_reminder_sent=False
    ).select_related('staff', 'schedule__restaurant')
    
    service = NotificationService()
    count = 0
    
    for shift in upcoming_shifts:
        if shift.staff.phone and shift.task_templates.exists():
            try:
                service.send_shift_notification(shift, notification_type='CHECKLIST_REMINDER')
                shift.check_list_reminder_sent = True
                shift.save(update_fields=['check_list_reminder_sent'])
                count += 1
                logger.info(f"Sent checklist reminder to {shift.staff.email} for shift {shift.id}")
            except Exception as e:
                logger.error(f"Failed to send checklist reminder for shift {shift.id}: {e}")
    
    logger.info(f"Sent {count} checklist reminders")
    return f"Sent {count} checklist reminders"


def _shift_recipients(shift):
    """Deduplicated list of staff to notify for this shift (legacy staff + staff_members)."""
    seen = set()
    out = []
    if shift.staff_id and shift.staff:
        seen.add(shift.staff_id)
        out.append(shift.staff)
    for m in shift.staff_members.all():
        if m.id not in seen and m:
            seen.add(m.id)
            out.append(m)
    return out


@shared_task
def send_clock_in_reminders():
    """
    Send clock-in reminders 10 minutes before shift.
    Uses WhatsApp template (staff_clock_in / WHATSAPP_TEMPLATE_STAFF_CLOCK_IN) via Miya (Lua) or directly.
    Runs every 5 minutes via Celery Beat.
    """
    now = timezone.now()
    upcoming_shifts = AssignedShift.objects.filter(
        shift_date=now.date(),
        start_time__gte=now + timedelta(minutes=5),
        start_time__lte=now + timedelta(minutes=15),
        clock_in_reminder_sent=False,
        status__in=['SCHEDULED', 'CONFIRMED']
    ).select_related('staff', 'schedule__restaurant').prefetch_related('staff_members')
    
    service = NotificationService()
    count = 0
    
    for shift in upcoming_shifts:
        recipients = _shift_recipients(shift)
        sent_any = False
        for member in recipients:
            if getattr(member, 'phone', None):
                try:
                    service.send_shift_notification(shift, notification_type='CLOCK_IN_REMINDER', recipient=member)
                    sent_any = True
                    count += 1
                    logger.info(f"Sent clock-in reminder to {member.email} for shift {shift.id}")
                except Exception as e:
                    logger.error(f"Failed to send clock-in reminder for shift {shift.id} to {member.email}: {e}")
        if sent_any:
            shift.clock_in_reminder_sent = True
            shift.save(update_fields=['clock_in_reminder_sent'])
    
    logger.info(f"Sent {count} clock-in reminders")
    return f"Sent {count} clock-in reminders"


@shared_task
def send_weekly_schedule_notifications():
    """
    Send weekly schedule notifications when published
    Triggered manually or by schedule publication
    """
    from scheduling.models import WeeklySchedule
    from django.db.models import Count
    
    # Find schedules published in the last hour that haven't been notified
    one_hour_ago = timezone.now() - timedelta(hours=1)
    schedules = WeeklySchedule.objects.filter(
        created_at__gte=one_hour_ago,
        is_published=True
    ).annotate(
        shift_count=Count('assigned_shifts')
    ).filter(shift_count__gt=0)
    
    service = NotificationService()
    total_sent = 0
    
    for schedule in schedules:
        # Get unique staff members in this schedule
        shifts = schedule.assigned_shifts.select_related('staff').distinct('staff')
        
        for shift in shifts:
            if shift.staff.phone:
                try:
                    service.send_shift_notification(shift, notification_type='SCHEDULE_PUBLISHED')
                    total_sent += 1
                except Exception as e:
                    logger.error(f"Failed to send schedule notification to {shift.staff.email}: {e}")
    
    logger.info(f"Sent {total_sent} weekly schedule notifications")
    return f"Sent {total_sent} schedule notifications"
