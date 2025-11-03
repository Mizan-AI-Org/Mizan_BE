from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
import logging

from notifications.signals import (
    send_shift_reminders,
    send_overdue_task_notifications,
    send_compliance_alerts
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Send scheduled notifications (shift reminders, overdue tasks, compliance alerts)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--type',
            type=str,
            choices=['reminders', 'overdue', 'compliance', 'all'],
            default='all',
            help='Type of notifications to send'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be sent without actually sending'
        )

    def handle(self, *args, **options):
        notification_type = options['type']
        dry_run = options['dry_run']
        
        if dry_run:
            self.stdout.write(
                self.style.WARNING('DRY RUN MODE - No notifications will be sent')
            )
        
        try:
            if notification_type in ['reminders', 'all']:
                self.send_shift_reminders(dry_run)
            
            if notification_type in ['overdue', 'all']:
                self.send_overdue_notifications(dry_run)
            
            if notification_type in ['compliance', 'all']:
                self.send_compliance_notifications(dry_run)
                
        except Exception as e:
            logger.error(f"Error in scheduled notifications: {str(e)}")
            self.stdout.write(
                self.style.ERROR(f'Error: {str(e)}')
            )

    def send_shift_reminders(self, dry_run=False):
        """Send shift reminder notifications"""
        self.stdout.write('Processing shift reminders...')
        
        if dry_run:
            from scheduling.models import AssignedShift
            
            # Get shifts starting in the next 2 hours
            reminder_time = timezone.now() + timedelta(hours=2)
            upcoming_shifts = AssignedShift.objects.filter(
                start_time__lte=reminder_time,
                start_time__gt=timezone.now(),
                status='CONFIRMED'
            )
            
            self.stdout.write(
                f'Would send reminders for {upcoming_shifts.count()} upcoming shifts:'
            )
            for shift in upcoming_shifts[:5]:  # Show first 5
                self.stdout.write(
                    f'  - {shift.staff.get_full_name()}: {shift.start_time}'
                )
            if upcoming_shifts.count() > 5:
                self.stdout.write(f'  ... and {upcoming_shifts.count() - 5} more')
        else:
            send_shift_reminders()
            self.stdout.write(
                self.style.SUCCESS('Shift reminders sent successfully')
            )

    def send_overdue_notifications(self, dry_run=False):
        """Send overdue task notifications"""
        self.stdout.write('Processing overdue task notifications...')
        
        if dry_run:
            from tasks.models import Task
            
            overdue_tasks = Task.objects.filter(
                due_date__lt=timezone.now(),
                status__in=['PENDING', 'IN_PROGRESS']
            )
            
            self.stdout.write(
                f'Would send overdue notifications for {overdue_tasks.count()} tasks:'
            )
            for task in overdue_tasks[:5]:  # Show first 5
                self.stdout.write(
                    f'  - {task.title} (assigned to: {task.assigned_to.get_full_name() if task.assigned_to else "Unassigned"})'
                )
            if overdue_tasks.count() > 5:
                self.stdout.write(f'  ... and {overdue_tasks.count() - 5} more')
        else:
            send_overdue_task_notifications()
            self.stdout.write(
                self.style.SUCCESS('Overdue task notifications sent successfully')
            )

    def send_compliance_notifications(self, dry_run=False):
        """Send compliance alert notifications"""
        self.stdout.write('Processing compliance alerts...')
        
        if dry_run:
            from scheduling.models import AssignedShift
            
            shifts_needing_briefing = AssignedShift.objects.filter(
                safety_briefing_required=True,
                safety_briefing_completed=False,
                start_time__lte=timezone.now() + timedelta(hours=24),
                start_time__gt=timezone.now()
            )
            
            self.stdout.write(
                f'Would send compliance alerts for {shifts_needing_briefing.count()} shifts:'
            )
            for shift in shifts_needing_briefing[:5]:  # Show first 5
                self.stdout.write(
                    f'  - {shift.staff.get_full_name()}: {shift.start_time} (Safety briefing required)'
                )
            if shifts_needing_briefing.count() > 5:
                self.stdout.write(f'  ... and {shifts_needing_briefing.count() - 5} more')
        else:
            send_compliance_alerts()
            self.stdout.write(
                self.style.SUCCESS('Compliance alerts sent successfully')
            )