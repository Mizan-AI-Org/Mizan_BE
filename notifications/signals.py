from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone
from datetime import timedelta
import logging

from .services import notification_service
from .models import NotificationPreference


from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from .models import Notification
from .serializers import NotificationSerializer
logger = logging.getLogger(__name__)

@receiver(post_save, sender='scheduling.AssignedShift')
def handle_shift_notifications(sender, instance, created, **kwargs):
    """Handle notifications when shifts are created or updated"""
    try:
        if created:
            # New shift assigned
            notification_service.send_shift_notification(
                shift=instance,
                notification_type='SHIFT_ASSIGNED'
            )
            logger.info(f"Shift assignment notification sent for shift {instance.id}")
        else:
            # Existing shift updated - check if important fields changed
            if hasattr(instance, '_state') and instance._state.adding is False:
                # Get the previous version to compare changes
                try:
                    old_instance = sender.objects.get(pk=instance.pk)
                    
                    # Check if time, location, or other important details changed
                    important_fields_changed = (
                        old_instance.start_time != instance.start_time or
                        old_instance.end_time != instance.end_time or
                        old_instance.workspace_location != instance.workspace_location or
                        old_instance.department != instance.department or
                        old_instance.status != instance.status
                    )
                    
                    if important_fields_changed:
                        if instance.status == 'CANCELLED':
                            notification_service.send_shift_notification(
                                shift=instance,
                                notification_type='SHIFT_CANCELLED'
                            )
                        else:
                            notification_service.send_shift_notification(
                                shift=instance,
                                notification_type='SHIFT_UPDATED'
                            )
                        logger.info(f"Shift update notification sent for shift {instance.id}")
                        
                except sender.DoesNotExist:
                    pass  # Original instance not found, treat as new
                    
    except Exception as e:
        logger.error(f"Failed to send shift notification for shift {instance.id}: {str(e)}")


@receiver(post_save, sender='staff.StaffAvailability')
def handle_availability_notifications(sender, instance, created, **kwargs):
    """Handle notifications for availability requests"""
    try:
        if created and instance.availability_type == 'TIME_OFF':
            # New time-off request - notify managers
            from accounts.models import CustomUser
            
            # Get managers/supervisors to notify
            managers = CustomUser.objects.filter(
                role__in=['MANAGER', 'ADMIN'],
                restaurant=instance.staff.restaurant
            )
            
            for manager in managers:
                notification_service.send_custom_notification(
                    recipient=manager,
                    message=f"New time-off request from {instance.staff.get_full_name()}\n"
                           f"Date: {instance.specific_date or 'Recurring'}\n"
                           f"Reason: {instance.reason or 'Not specified'}",
                    notification_type='AVAILABILITY_REQUEST',
                    channels=['app', 'email']
                )
            
            logger.info(f"Availability request notification sent for request {instance.id}")
            
        elif not created and instance.availability_type == 'TIME_OFF':
            # Status changed - notify the staff member
            if instance.status in ['APPROVED', 'DENIED']:
                status_message = "approved" if instance.status == 'APPROVED' else "denied"
                notification_type = f'AVAILABILITY_{instance.status}'
                
                notification_service.send_custom_notification(
                    recipient=instance.staff,
                    message=f"Your time-off request has been {status_message}\n"
                           f"Date: {instance.specific_date or 'Recurring'}\n"
                           f"Manager notes: {instance.approval_notes or 'None'}",
                    notification_type=notification_type,
                    channels=['app', 'whatsapp', 'email']
                )
                
                logger.info(f"Availability status notification sent for request {instance.id}")
                
    except Exception as e:
        logger.error(f"Failed to send availability notification for request {instance.id}: {str(e)}")


@receiver(post_save, sender='scheduling.Task')
def handle_task_notifications(sender, instance, created, **kwargs):
    """Handle notifications for task assignments and updates"""
    try:
        if created and instance.assigned_to:
            # New task assigned
            notification_service.send_custom_notification(
                recipient=instance.assigned_to,
                message=f"New task assigned: {instance.title}\n"
                       f"Due: {instance.due_date.strftime('%Y-%m-%d %H:%M') if instance.due_date else 'No due date'}\n"
                       f"Priority: {instance.get_priority_display()}\n"
                       f"Description: {instance.description[:100]}{'...' if len(instance.description) > 100 else ''}",
                notification_type='TASK_ASSIGNED',
                channels=['app', 'push']
            )
            logger.info(f"Task assignment notification sent for task {instance.id}")
            
        elif not created and instance.status == 'COMPLETED':
            # Task completed - notify supervisor if exists
            if hasattr(instance, 'created_by') and instance.created_by:
                notification_service.send_custom_notification(
                    recipient=instance.created_by,
                    message=f"Task completed: {instance.title}\n"
                           f"Completed by: {instance.assigned_to.get_full_name() if instance.assigned_to else 'Unknown'}\n"
                           f"Completion time: {timezone.now().strftime('%Y-%m-%d %H:%M')}",
                    notification_type='TASK_COMPLETED',
                    channels=['app']
                )
                logger.info(f"Task completion notification sent for task {instance.id}")
                
    except Exception as e:
        logger.error(f"Failed to send task notification for task {instance.id}: {str(e)}")


@receiver(post_save, sender='accounts.CustomUser')
def create_notification_preferences(sender, instance, created, **kwargs):
    """Create default notification preferences for new users"""
    if created:
        try:
            NotificationPreference.objects.get_or_create(
                user=instance,
                defaults={
                    'email_enabled': True,
                    'push_enabled': True,
                    'whatsapp_enabled': True,
                    'shift_notifications': True,
                    'task_notifications': True,
                    'availability_notifications': True,
                    'compliance_notifications': True,
                    'emergency_notifications': True,
                    'announcement_notifications': True,
                }
            )
            logger.info(f"Default notification preferences created for user {instance.id}")
        except Exception as e:
            logger.error(f"Failed to create notification preferences for user {instance.id}: {str(e)}")


def send_shift_reminders():
    """
    Function to send shift reminders (to be called by a scheduled task)
    This should be called by a cron job or Celery task
    """
    try:
        from scheduling.models import AssignedShift
        
        # Get shifts starting in the next 2 hours
        reminder_time = timezone.now() + timedelta(hours=2)
        upcoming_shifts = AssignedShift.objects.filter(
            start_time__lte=reminder_time,
            start_time__gt=timezone.now(),
            status='CONFIRMED'
        )
        
        for shift in upcoming_shifts:
            # Check if reminder already sent
            if not hasattr(shift, 'reminder_sent') or not shift.reminder_sent:
                notification_service.send_shift_notification(
                    shift=shift,
                    notification_type='SHIFT_REMINDER'
                )
                
                # Mark reminder as sent (you might want to add this field to the model)
                # shift.reminder_sent = True
                # shift.save(update_fields=['reminder_sent'])
                
        logger.info(f"Sent reminders for {upcoming_shifts.count()} upcoming shifts")
        
    except Exception as e:
        logger.error(f"Failed to send shift reminders: {str(e)}")


def send_overdue_task_notifications():
    """
    Function to send notifications for overdue tasks
    This should be called by a scheduled task
    """
    try:
        from tasks.models import Task
        
        # Get overdue tasks
        overdue_tasks = Task.objects.filter(
            due_date__lt=timezone.now(),
            status__in=['PENDING', 'IN_PROGRESS']
        )
        
        for task in overdue_tasks:
            if task.assigned_to:
                notification_service.send_custom_notification(
                    recipient=task.assigned_to,
                    message=f"Task overdue: {task.title}\n"
                           f"Was due: {task.due_date.strftime('%Y-%m-%d %H:%M')}\n"
                           f"Please complete as soon as possible.",
                    notification_type='TASK_OVERDUE',
                    channels=['app', 'push', 'email']
                )
                
        logger.info(f"Sent overdue notifications for {overdue_tasks.count()} tasks")
        
    except Exception as e:
        logger.error(f"Failed to send overdue task notifications: {str(e)}")


def send_compliance_alerts():
    """
    Function to send compliance-related notifications
    This should be called by a scheduled task
    """
    try:
        from scheduling.models import AssignedShift
        
        # Get shifts requiring safety briefing that haven't been completed
        shifts_needing_briefing = AssignedShift.objects.filter(
            safety_briefing_required=True,
            safety_briefing_completed=False,
            start_time__lte=timezone.now() + timedelta(hours=24),  # Starting within 24 hours
            start_time__gt=timezone.now()
        )
        
        for shift in shifts_needing_briefing:
            notification_service.send_custom_notification(
                recipient=shift.staff,
                message=f"Safety briefing required for upcoming shift\n"
                       f"Shift: {shift.start_time.strftime('%Y-%m-%d %H:%M')}\n"
                       f"Location: {shift.workspace_location or 'Main Area'}\n"
                       f"Please complete your safety briefing before the shift starts.",
                notification_type='COMPLIANCE_ALERT',
                channels=['app', 'whatsapp', 'email']
            )
            
        logger.info(f"Sent compliance alerts for {shifts_needing_briefing.count()} shifts")
        
    except Exception as e:
        logger.error(f"Failed to send compliance alerts: {str(e)}")


@receiver(post_save, sender=Notification)
def send_realtime_notification(sender, instance, created, **kwargs):
    if created:  # only for new notifications
        channel_layer = get_channel_layer()
        data = NotificationSerializer(instance).data
        
        # Group name = user_<id>_notifications (as used in NotificationConsumer)
        group_name = f'user_{instance.recipient.id}_notifications'
        
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                'type': 'send_notification',
                'notification': data
            }
        )
