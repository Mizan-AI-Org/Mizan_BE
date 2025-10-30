"""
Signal handlers for staff app
"""
import logging
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver
from .models import Schedule, ScheduleChange
from .backup_service import ScheduleBackupService

logger = logging.getLogger(__name__)

@receiver(post_save, sender=Schedule)
def backup_schedule_on_save(sender, instance, created, **kwargs):
    """Create a backup when a schedule is saved"""
    if not created:  # Only backup on updates, not on creation
        try:
            backup_service = ScheduleBackupService()
            backup_path = backup_service.create_backup(instance)
            if backup_path:
                logger.info(f"Auto-backup created for schedule {instance.id}")
            else:
                logger.warning(f"Failed to create auto-backup for schedule {instance.id}")
        except Exception as e:
            logger.error(f"Error in backup_schedule_on_save: {str(e)}")

@receiver(pre_delete, sender=Schedule)
def backup_schedule_before_delete(sender, instance, **kwargs):
    """Create a backup before a schedule is deleted"""
    try:
        backup_service = ScheduleBackupService()
        backup_path = backup_service.create_backup(instance)
        if backup_path:
            logger.info(f"Auto-backup created before deletion of schedule {instance.id}")
        else:
            logger.warning(f"Failed to create auto-backup before deletion of schedule {instance.id}")
    except Exception as e:
        logger.error(f"Error in backup_schedule_before_delete: {str(e)}")