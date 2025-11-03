"""
Backup Service for Staff Management System
Provides automated backup functionality for schedule data
"""
import json
import logging
import os
from datetime import datetime
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from .models import Schedule, ScheduleChange

logger = logging.getLogger(__name__)

class ScheduleBackupService:
    """Service for managing schedule data backups"""
    
    def __init__(self):
        """Initialize the backup service"""
        self.backup_dir = os.path.join(settings.BASE_DIR, 'backups', 'schedules')
        os.makedirs(self.backup_dir, exist_ok=True)
        
    def create_backup(self, schedule):
        """Create a backup of a schedule"""
        try:
            # Create a JSON representation of the schedule
            schedule_data = {
                'id': str(schedule.id),
                'title': schedule.title,
                'staff_id': str(schedule.staff.id),
                'start_time': schedule.start_time.isoformat(),
                'end_time': schedule.end_time.isoformat(),
                'tasks': schedule.tasks,
                'is_recurring': schedule.is_recurring,
                'recurrence_pattern': schedule.recurrence_pattern,
                'recurrence_end_date': schedule.recurrence_end_date.isoformat() if schedule.recurrence_end_date else None,
                'color': schedule.color,
                'status': schedule.status,
                'created_at': schedule.created_at.isoformat(),
                'updated_at': schedule.updated_at.isoformat(),
                'created_by_id': str(schedule.created_by.id) if schedule.created_by else None,
                'last_modified_by_id': str(schedule.last_modified_by.id) if schedule.last_modified_by else None,
            }
            
            # Create a backup file
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_filename = f"schedule_{schedule.id}_{timestamp}.json"
            backup_path = os.path.join(self.backup_dir, backup_filename)
            
            with open(backup_path, 'w') as f:
                json.dump(schedule_data, f, cls=DjangoJSONEncoder, indent=4)
                
            logger.info(f"Backup created for schedule {schedule.id}: {backup_path}")
            return backup_path
        except Exception as e:
            logger.error(f"Error creating backup for schedule {schedule.id}: {str(e)}")
            return None
    
    def restore_from_backup(self, schedule_id, backup_file=None):
        """Restore a schedule from backup"""
        try:
            if backup_file:
                backup_path = backup_file
            else:
                # Find the latest backup for this schedule
                backup_files = [f for f in os.listdir(self.backup_dir) if f.startswith(f"schedule_{schedule_id}_")]
                if not backup_files:
                    logger.error(f"No backup found for schedule {schedule_id}")
                    return False
                
                # Sort by timestamp (newest first)
                backup_files.sort(reverse=True)
                backup_path = os.path.join(self.backup_dir, backup_files[0])
            
            # Load the backup data
            with open(backup_path, 'r') as f:
                backup_data = json.load(f)
            
            # Restore the schedule
            with transaction.atomic():
                try:
                    schedule = Schedule.objects.get(id=schedule_id)
                    
                    # Update the schedule with backup data
                    schedule.title = backup_data['title']
                    schedule.start_time = datetime.fromisoformat(backup_data['start_time'])
                    schedule.end_time = datetime.fromisoformat(backup_data['end_time'])
                    schedule.tasks = backup_data['tasks']
                    schedule.is_recurring = backup_data['is_recurring']
                    schedule.recurrence_pattern = backup_data['recurrence_pattern']
                    if backup_data['recurrence_end_date']:
                        schedule.recurrence_end_date = datetime.fromisoformat(backup_data['recurrence_end_date'])
                    schedule.color = backup_data['color']
                    schedule.status = backup_data['status']
                    
                    # Save the restored schedule
                    schedule.save()
                    
                    logger.info(f"Schedule {schedule_id} restored from backup: {backup_path}")
                    return True
                except Schedule.DoesNotExist:
                    logger.error(f"Schedule {schedule_id} not found")
                    return False
        except Exception as e:
            logger.error(f"Error restoring backup for schedule {schedule_id}: {str(e)}")
            return False
    
    def create_all_backups(self):
        """Create backups for all schedules"""
        schedules = Schedule.objects.all()
        backup_count = 0
        
        for schedule in schedules:
            if self.create_backup(schedule):
                backup_count += 1
        
        logger.info(f"Created {backup_count} schedule backups")
        return backup_count
    
    def get_backup_history(self, schedule_id):
        """Get backup history for a schedule"""
        backup_files = [f for f in os.listdir(self.backup_dir) if f.startswith(f"schedule_{schedule_id}_")]
        backup_files.sort(reverse=True)  # Newest first
        
        history = []
        for backup_file in backup_files:
            # Extract timestamp from filename
            parts = backup_file.split('_')
            if len(parts) >= 3:
                timestamp_str = parts[2].split('.')[0]
                try:
                    timestamp = datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')
                    history.append({
                        'filename': backup_file,
                        'timestamp': timestamp.isoformat(),
                        'path': os.path.join(self.backup_dir, backup_file)
                    })
                except ValueError:
                    continue
        
        return history