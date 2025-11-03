"""
Django management command for audit system maintenance
Provides commands for cleaning up old audit logs, generating reports, and system maintenance
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.db.models import Count, Q
from datetime import datetime, timedelta
import csv
import json
import os

from scheduling.audit import AuditLog, AuditSeverity, AuditActionType


class Command(BaseCommand):
    help = 'Audit system maintenance commands'

    def add_arguments(self, parser):
        parser.add_argument(
            'action',
            choices=['cleanup', 'report', 'export', 'stats', 'archive'],
            help='Action to perform'
        )
        
        parser.add_argument(
            '--days',
            type=int,
            default=90,
            help='Number of days to keep/process (default: 90)'
        )
        
        parser.add_argument(
            '--severity',
            choices=['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'],
            help='Filter by severity level'
        )
        
        parser.add_argument(
            '--action-type',
            choices=[choice[0] for choice in AuditActionType.choices],
            help='Filter by action type'
        )
        
        parser.add_argument(
            '--user',
            type=str,
            help='Filter by user email'
        )
        
        parser.add_argument(
            '--restaurant',
            type=int,
            help='Filter by restaurant ID'
        )
        
        parser.add_argument(
            '--output',
            type=str,
            help='Output file path for export/report'
        )
        
        parser.add_argument(
            '--format',
            choices=['csv', 'json'],
            default='csv',
            help='Output format (default: csv)'
        )
        
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without actually doing it'
        )

    def handle(self, *args, **options):
        action = options['action']
        
        if action == 'cleanup':
            self.cleanup_old_logs(options)
        elif action == 'report':
            self.generate_report(options)
        elif action == 'export':
            self.export_logs(options)
        elif action == 'stats':
            self.show_statistics(options)
        elif action == 'archive':
            self.archive_logs(options)

    def cleanup_old_logs(self, options):
        """Clean up old audit logs"""
        days = options['days']
        dry_run = options['dry_run']
        
        cutoff_date = timezone.now() - timedelta(days=days)
        
        # Build query
        query = Q(timestamp__lt=cutoff_date)
        
        # Don't delete critical logs by default
        if not options.get('severity'):
            query &= ~Q(severity=AuditSeverity.CRITICAL)
        
        logs_to_delete = AuditLog.objects.filter(query)
        count = logs_to_delete.count()
        
        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f'DRY RUN: Would delete {count} audit logs older than {days} days'
                )
            )
            return
        
        if count == 0:
            self.stdout.write(
                self.style.SUCCESS('No audit logs to clean up')
            )
            return
        
        # Confirm deletion
        confirm = input(f'Delete {count} audit logs? (y/N): ')
        if confirm.lower() != 'y':
            self.stdout.write('Cancelled')
            return
        
        deleted_count, _ = logs_to_delete.delete()
        
        self.stdout.write(
            self.style.SUCCESS(
                f'Successfully deleted {deleted_count} audit logs'
            )
        )

    def generate_report(self, options):
        """Generate audit report"""
        days = options['days']
        output_file = options.get('output')
        format_type = options['format']
        
        # Date range
        end_date = timezone.now()
        start_date = end_date - timedelta(days=days)
        
        # Build query
        query = Q(timestamp__gte=start_date, timestamp__lte=end_date)
        
        if options.get('severity'):
            query &= Q(severity=options['severity'])
        
        if options.get('action_type'):
            query &= Q(action=options['action_type'])
        
        if options['user']:
            query &= Q(user__email=options['user'])
        
        if options.get('restaurant'):
            query &= Q(restaurant_id=options['restaurant'])
        
        logs = AuditLog.objects.filter(query).select_related(
            'user', 'content_type', 'restaurant'
        ).order_by('-timestamp')
        
        # Generate statistics
        stats = {
            'total_logs': logs.count(),
            'date_range': {
                'start': start_date.isoformat(),
                'end': end_date.isoformat()
            },
            'by_severity': dict(
                logs.values('severity').annotate(count=Count('id')).values_list('severity', 'count')
            ),
            'by_action': dict(
                logs.values('action').annotate(count=Count('id')).values_list('action', 'count')
            ),
            'by_user': dict(
            logs.filter(user__isnull=False).values('user__email').annotate(
                count=Count('id')
            ).values_list('user__email', 'count')[:10]
        ),
            'by_restaurant': dict(
                logs.filter(restaurant__isnull=False).values('restaurant__name').annotate(
                    count=Count('id')
                ).values_list('restaurant__name', 'count')
            )
        }
        
        # Output report
        if output_file:
            if format_type == 'json':
                self._export_json_report(logs, stats, output_file)
            else:
                self._export_csv_report(logs, stats, output_file)
        else:
            self._print_report(stats)

    def export_logs(self, options):
        """Export audit logs"""
        days = options['days']
        output_file = options.get('output')
        format_type = options['format']
        
        if not output_file:
            raise CommandError('Output file is required for export')
        
        # Date range
        end_date = timezone.now()
        start_date = end_date - timedelta(days=days)
        
        # Build query
        query = Q(timestamp__gte=start_date, timestamp__lte=end_date)
        
        if options.get('severity'):
            query &= Q(severity=options['severity'])
        
        if options.get('action_type'):
            query &= Q(action=options['action_type'])
        
        if options['user']:
            query &= Q(user__email=options['user'])
        
        if options.get('restaurant'):
            query &= Q(restaurant_id=options['restaurant'])
        
        logs = AuditLog.objects.filter(query).select_related(
            'user', 'content_type', 'restaurant'
        ).order_by('-timestamp')
        
        if format_type == 'json':
            self._export_logs_json(logs, output_file)
        else:
            self._export_logs_csv(logs, output_file)
        
        self.stdout.write(
            self.style.SUCCESS(
                f'Exported {logs.count()} audit logs to {output_file}'
            )
        )

    def show_statistics(self, options):
        """Show audit statistics"""
        days = options['days']
        
        # Date range
        end_date = timezone.now()
        start_date = end_date - timedelta(days=days)
        
        logs = AuditLog.objects.filter(
            timestamp__gte=start_date,
            timestamp__lte=end_date
        )
        
        # Basic stats
        total_logs = logs.count()
        
        # By severity
        severity_stats = logs.values('severity').annotate(
            count=Count('id')
        ).order_by('-count')
        
        # By action
        action_stats = logs.values('action').annotate(
            count=Count('id')
        ).order_by('-count')
        
        # By user
        user_stats = logs.filter(user__isnull=False).values(
            'user__email'
        ).annotate(count=Count('id')).order_by('-count')[:10]
        
        # By restaurant
        restaurant_stats = logs.filter(restaurant__isnull=False).values(
            'restaurant__name'
        ).annotate(count=Count('id')).order_by('-count')
        
        # Recent activity (last 24 hours)
        recent_cutoff = timezone.now() - timedelta(hours=24)
        recent_logs = logs.filter(timestamp__gte=recent_cutoff).count()
        
        # Print statistics
        self.stdout.write(
            self.style.SUCCESS(f'\n=== Audit Statistics (Last {days} days) ===')
        )
        self.stdout.write(f'Total logs: {total_logs}')
        self.stdout.write(f'Recent activity (24h): {recent_logs}')
        
        self.stdout.write('\n--- By Severity ---')
        for stat in severity_stats:
            severity_display = dict(AuditSeverity.choices)[stat['severity']]
            self.stdout.write(f'{severity_display}: {stat["count"]}')
        
        self.stdout.write('\n--- By Action ---')
        for stat in action_stats:
            action_display = dict(AuditActionType.choices)[stat['action']]
            self.stdout.write(f'{action_display}: {stat["count"]}')
        
        self.stdout.write('\n--- Top Users ---')
        for stat in user_stats:
            self.stdout.write(f'{stat["user__email"]}: {stat["count"]}')
        
        self.stdout.write('\n--- By Restaurant ---')
        for stat in restaurant_stats:
            self.stdout.write(f'{stat["restaurant__name"]}: {stat["count"]}')

    def archive_logs(self, options):
        """Archive old logs to file before deletion"""
        days = options['days']
        output_file = options.get('output')
        format_type = options['format']
        dry_run = options['dry_run']
        
        if not output_file:
            timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
            output_file = f'audit_archive_{timestamp}.{format_type}'
        
        cutoff_date = timezone.now() - timedelta(days=days)
        
        # Build query
        query = Q(timestamp__lt=cutoff_date)
        
        logs_to_archive = AuditLog.objects.filter(query).select_related(
            'user', 'content_type', 'restaurant'
        ).order_by('-timestamp')
        
        count = logs_to_archive.count()
        
        if count == 0:
            self.stdout.write(
                self.style.SUCCESS('No audit logs to archive')
            )
            return
        
        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f'DRY RUN: Would archive {count} audit logs to {output_file}'
                )
            )
            return
        
        # Export logs
        if format_type == 'json':
            self._export_logs_json(logs_to_archive, output_file)
        else:
            self._export_logs_csv(logs_to_archive, output_file)
        
        # Confirm deletion after archive
        confirm = input(f'Archive created. Delete {count} archived logs? (y/N): ')
        if confirm.lower() == 'y':
            deleted_count, _ = logs_to_archive.delete()
            self.stdout.write(
                self.style.SUCCESS(
                    f'Archived and deleted {deleted_count} audit logs to {output_file}'
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Archived {count} audit logs to {output_file} (not deleted)'
                )
            )

    def _export_logs_csv(self, logs, output_file):
        """Export logs to CSV format"""
        with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            
            # Header
            writer.writerow([
                'Timestamp',
                'User',
                'Action',
                'Severity',
                'Description',
                'Content Type',
                'Object ID',
                'IP Address',
                'User Agent',
                'Restaurant'
            ])
            
            # Data
            for log in logs:
                writer.writerow([
                    log.timestamp.isoformat(),
                    log.user.email if log.user else 'System',
                    log.get_action_display(),
                    log.get_severity_display(),
                    log.description,
                    str(log.content_type) if log.content_type else '',
                    log.object_id or '',
                    log.ip_address or '',
                    log.user_agent or '',
                    log.restaurant.name if log.restaurant else ''
                ])

    def _export_logs_json(self, logs, output_file):
        """Export logs to JSON format"""
        data = []
        for log in logs:
            data.append({
                'timestamp': log.timestamp.isoformat(),
                'user': {
                    'id': log.user.id if log.user else None,
                    'username': log.user.email if log.user else None,
                    'full_name': log.user.get_full_name() if log.user else None
                },
                'action': log.action,
                'action_display': log.get_action_display(),
                'severity': log.severity,
                'severity_display': log.get_severity_display(),
                'description': log.description,
                'content_type': str(log.content_type) if log.content_type else None,
                'object_id': log.object_id,
                'old_values': log.old_values,
                'new_values': log.new_values,
                'metadata': log.metadata,
                'ip_address': log.ip_address,
                'user_agent': log.user_agent,
                'restaurant': {
                    'id': log.restaurant.id if log.restaurant else None,
                    'name': log.restaurant.name if log.restaurant else None
                }
            })
        
        with open(output_file, 'w', encoding='utf-8') as jsonfile:
            json.dump(data, jsonfile, indent=2, ensure_ascii=False)

    def _export_csv_report(self, logs, stats, output_file):
        """Export report in CSV format"""
        # Implementation for CSV report export
        pass

    def _export_json_report(self, logs, stats, output_file):
        """Export report in JSON format"""
        report_data = {
            'generated_at': timezone.now().isoformat(),
            'statistics': stats,
            'logs': []
        }
        
        for log in logs[:1000]:  # Limit to first 1000 logs
            report_data['logs'].append({
                'timestamp': log.timestamp.isoformat(),
                'user': log.user.email if log.user else 'System',
                'action': log.get_action_display(),
                'severity': log.get_severity_display(),
                'description': log.description,
                'ip_address': log.ip_address
            })
        
        with open(output_file, 'w', encoding='utf-8') as jsonfile:
            json.dump(report_data, jsonfile, indent=2, ensure_ascii=False)

    def _print_report(self, stats):
        """Print report to console"""
        self.stdout.write(
            self.style.SUCCESS('\n=== Audit Report ===')
        )
        self.stdout.write(f'Total logs: {stats["total_logs"]}')
        self.stdout.write(f'Date range: {stats["date_range"]["start"]} to {stats["date_range"]["end"]}')
        
        self.stdout.write('\n--- By Severity ---')
        for severity, count in stats['by_severity'].items():
            severity_display = dict(AuditSeverity.choices)[severity]
            self.stdout.write(f'{severity_display}: {count}')
        
        self.stdout.write('\n--- By Action ---')
        for action, count in stats['by_action'].items():
            action_display = dict(AuditActionType.choices)[action]
            self.stdout.write(f'{action_display}: {count}')
        
        self.stdout.write('\n--- Top Users ---')
        for email, count in list(stats['by_user'].items())[:10]:
            self.stdout.write(f'{email}: {count}')