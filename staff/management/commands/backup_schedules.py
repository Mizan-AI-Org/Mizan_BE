"""
Management command to backup all schedule data
"""
from django.core.management.base import BaseCommand
from staff.backup_service import ScheduleBackupService
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Create backups of all schedule data'

    def handle(self, *args, **options):
        backup_service = ScheduleBackupService()
        backup_count = backup_service.create_all_backups()
        
        self.stdout.write(
            self.style.SUCCESS(f'Successfully created {backup_count} schedule backups')
        )