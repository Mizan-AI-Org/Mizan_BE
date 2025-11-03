from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
import logging

from notifications.models import Notification, NotificationLog, DeviceToken

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Clean up old notifications and logs to maintain database performance'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=90,
            help='Delete notifications older than this many days (default: 90)'
        )
        parser.add_argument(
            '--log-days',
            type=int,
            default=30,
            help='Delete notification logs older than this many days (default: 30)'
        )
        parser.add_argument(
            '--inactive-token-days',
            type=int,
            default=180,
            help='Delete inactive device tokens older than this many days (default: 180)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting'
        )
        parser.add_argument(
            '--keep-unread',
            action='store_true',
            help='Keep unread notifications regardless of age'
        )

    def handle(self, *args, **options):
        days = options['days']
        log_days = options['log_days']
        inactive_token_days = options['inactive_token_days']
        dry_run = options['dry_run']
        keep_unread = options['keep_unread']
        
        if dry_run:
            self.stdout.write(
                self.style.WARNING('DRY RUN MODE - Nothing will be deleted')
            )
        
        try:
            # Clean up old notifications
            self.cleanup_notifications(days, dry_run, keep_unread)
            
            # Clean up old notification logs
            self.cleanup_notification_logs(log_days, dry_run)
            
            # Clean up inactive device tokens
            self.cleanup_inactive_tokens(inactive_token_days, dry_run)
            
            if not dry_run:
                self.stdout.write(
                    self.style.SUCCESS('Cleanup completed successfully')
                )
                
        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}")
            self.stdout.write(
                self.style.ERROR(f'Error: {str(e)}')
            )

    def cleanup_notifications(self, days, dry_run=False, keep_unread=False):
        """Clean up old notifications"""
        cutoff_date = timezone.now() - timedelta(days=days)
        
        # Build queryset
        queryset = Notification.objects.filter(created_at__lt=cutoff_date)
        
        if keep_unread:
            # Only delete read notifications
            queryset = queryset.filter(read_at__isnull=False)
            self.stdout.write(f'Processing read notifications older than {days} days...')
        else:
            self.stdout.write(f'Processing all notifications older than {days} days...')
        
        count = queryset.count()
        
        if dry_run:
            self.stdout.write(f'Would delete {count} notifications')
            
            # Show some examples
            if count > 0:
                examples = queryset[:5]
                self.stdout.write('Examples:')
                for notification in examples:
                    status = 'read' if notification.read_at else 'unread'
                    self.stdout.write(
                        f'  - {notification.title} ({status}, {notification.created_at.date()})'
                    )
        else:
            if count > 0:
                queryset.delete()
                self.stdout.write(
                    self.style.SUCCESS(f'Deleted {count} old notifications')
                )
            else:
                self.stdout.write('No old notifications to delete')

    def cleanup_notification_logs(self, days, dry_run=False):
        """Clean up old notification logs"""
        cutoff_date = timezone.now() - timedelta(days=days)
        
        self.stdout.write(f'Processing notification logs older than {days} days...')
        
        queryset = NotificationLog.objects.filter(sent_at__lt=cutoff_date)
        count = queryset.count()
        
        if dry_run:
            self.stdout.write(f'Would delete {count} notification logs')
            
            # Show some examples
            if count > 0:
                examples = queryset[:5]
                self.stdout.write('Examples:')
                for log in examples:
                    self.stdout.write(
                        f'  - {log.notification.title} via {log.channel} ({log.sent_at.date()})'
                    )
        else:
            if count > 0:
                queryset.delete()
                self.stdout.write(
                    self.style.SUCCESS(f'Deleted {count} old notification logs')
                )
            else:
                self.stdout.write('No old notification logs to delete')

    def cleanup_inactive_tokens(self, days, dry_run=False):
        """Clean up inactive device tokens"""
        cutoff_date = timezone.now() - timedelta(days=days)
        
        self.stdout.write(f'Processing inactive device tokens older than {days} days...')
        
        queryset = DeviceToken.objects.filter(
            is_active=False,
            last_used__lt=cutoff_date
        )
        count = queryset.count()
        
        if dry_run:
            self.stdout.write(f'Would delete {count} inactive device tokens')
            
            # Show some examples
            if count > 0:
                examples = queryset[:5]
                self.stdout.write('Examples:')
                for token in examples:
                    self.stdout.write(
                        f'  - {token.user.email} ({token.device_type}, last used: {token.last_used.date()})'
                    )
        else:
            if count > 0:
                queryset.delete()
                self.stdout.write(
                    self.style.SUCCESS(f'Deleted {count} inactive device tokens')
                )
            else:
                self.stdout.write('No inactive device tokens to delete')

    def get_cleanup_stats(self):
        """Get statistics about what could be cleaned up"""
        now = timezone.now()
        
        # Notifications older than 90 days
        old_notifications = Notification.objects.filter(
            created_at__lt=now - timedelta(days=90)
        ).count()
        
        # Unread notifications older than 90 days
        old_unread = Notification.objects.filter(
            created_at__lt=now - timedelta(days=90),
            read_at__isnull=True
        ).count()
        
        # Logs older than 30 days
        old_logs = NotificationLog.objects.filter(
            sent_at__lt=now - timedelta(days=30)
        ).count()
        
        # Inactive tokens older than 180 days
        inactive_tokens = DeviceToken.objects.filter(
            is_active=False,
            last_used__lt=now - timedelta(days=180)
        ).count()
        
        self.stdout.write('\nCleanup Statistics:')
        self.stdout.write(f'  Notifications older than 90 days: {old_notifications}')
        self.stdout.write(f'  Unread notifications older than 90 days: {old_unread}')
        self.stdout.write(f'  Logs older than 30 days: {old_logs}')
        self.stdout.write(f'  Inactive tokens older than 180 days: {inactive_tokens}')