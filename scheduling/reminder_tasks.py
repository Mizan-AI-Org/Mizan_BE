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
    ).select_related('staff', 'weekly_schedule__restaurant')
    
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


@shared_task
def send_clock_in_reminders():
    """
    Send clock-in reminders 10 minutes before shift
    Runs every 5 minutes via Celery Beat
    """
    now = timezone.now()
    upcoming_shifts = AssignedShift.objects.filter(
        start_time__gte=now + timedelta(minutes=5),
        start_time__lte=now + timedelta(minutes=15),
        shift_date=now.date(),
        clock_in_reminder_sent=False
    ).select_related('staff', 'schedule__restaurant')
    
    service = NotificationService()
    count = 0
    
    for shift in upcoming_shifts:
        if shift.staff.phone:
            try:
                service.send_shift_notification(shift, notification_type='CLOCK_IN_REMINDER')
                shift.clock_in_reminder_sent = True
                shift.save(update_fields=['clock_in_reminder_sent'])
                count += 1
                logger.info(f"Sent clock-in reminder to {shift.staff.email} for shift {shift.id}")
            except Exception as e:
                logger.error(f"Failed to send clock-in reminder for shift {shift.id}: {e}")
    
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
